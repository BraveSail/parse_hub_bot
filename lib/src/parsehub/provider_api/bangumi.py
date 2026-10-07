"""bgm.tv（Bangumi 番组计划）日志（blog）解析。

**为什么抓网页而不是 API**（实测，不是文档没看全）：

- 官方 API（``api.bgm.tv``，OAS spec 46 个端点）**没有日志**这一类端点；
- ``api.bgm.tv/v0/blogs/<id>`` → **404**；``bgm.tv/blog/<id>.json`` → 200 但 **0 字节**。

⇒ 只能解析 HTML。代价是稳定性依赖页面结构（站点改版要跟着改选择器）。

**正文 HTML 是 bgm 自己的 BBCode 渲染器产出的**，标签对应关系以站内指南
（``bgm.tv/help/bbcode``）为准 —— 那页本身就是渲染器渲染的，是权威清单：

    [b]→<strong>        [i]→<em>
    [u]→<span style="text-decoration:underline;">
    [s]→<span style="text-decoration:line-through;">
    [mask]（马赛克）→<span style="background-color:#555; color:#555; border:1px solid #555;">
    [color=x]→<span style="color:x">        [size=n]→<span style="font-size:npt">
    [url]…[/url] / [url=x]y[/url]→<a class="l" href="x">
    [img]→<img class="code" src="…">
    表情（编辑器插入）→<img class="smile" alt="(bgm116)" src="/img/smiles/…">
    [quote]（指南未列，但真实日志里有）→<div class="quote"><q>…</q></div>

**页面实测**：日志页 ~22KB / 0.3s，**无限流、无 Cloudflare、图片无防盗链**
（带不带 Referer 都 200 同尺寸 ⇒ 不用像 B 站那样注入 Referer）。

⚠️ **必须显式按 utf-8 解码**：站点没在 header 里声明字符集，requests 会猜成 latin-1
（标题直接乱码，第一版探针就中招）。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger
from markdownify import MarkdownConverter

from ..types.platform import Platform
from ..utils import http
from ..utils.helpers import format_author_link, format_quote_block, profile_url, to_datetime

BLOG_API = "https://bgm.tv/blog/{blog_id}"
GROUP_TOPIC_API = "https://bgm.tv/group/topic/{topic_id}"
#: 条目讨论版（``/subject/topic/<id>``）—— **楼层结构与小组话题完全同构**，
#: 只有头部不同（归属是条目而不是小组，标题是页面上**第二个** ``h1``）
SUBJECT_TOPIC_API = "https://bgm.tv/subject/topic/{topic_id}"

#: 归属 → 规范页面模板。``/rakuen/topic/<归属>/<id>``（「超展开」里的入口）也走这两个
#: —— 它与规范路径是**同一个话题**（实测正文逐块相同），归一化后零新增解析逻辑。
_TOPIC_APIS = {"group": GROUP_TOPIC_API, "subject": SUBJECT_TOPIC_API}

#: 话题 URL 的三种入口。两条正则**互斥**（前者要 ``group|subject/topic/``，
#: 后者要 ``rakuen/topic/``），所以匹配顺序不影响结果 —— 实测两个方向都试过。
_TOPIC_URL_RE = re.compile(r"/(group|subject)/topic/(\d+)")
_RAKUEN_URL_RE = re.compile(r"/rakuen/topic/(group|subject)/(\d+)")

#: bgm 的**服务器时区**。页面上的时间是站点本地时间（``2026-10-7 00:24``，不带偏移），
#: 按 UTC 解释会让整条消息差 8 小时（用户报「时间好像有问题？多 8 小时」）。
#:
#: 证据：``api.bgm.tv`` 返回的时间戳明确带偏移 ——
#: ``{"updated_at":"2026-10-07T01:18:56+08:00"}``。
BGM_TIMEZONE = timezone(timedelta(hours=8))

#: 内容不存在时页面**仍然返回 HTTP 200**（不是 404），正文写「呜咕，出错了 数据库中没有…」
#: ⇒ 判据只能是「没有正文容器」，不能看状态码。
#: 文案按内容类型变化（「该日志」/「该小组话题」），所以只匹配共同的那半句。
_ERROR_MARKER = "数据库中没有查询到"

#: 马赛克（``[mask]``）的 style 特征 —— 前后都是 #555 的实心块
_MASK_MARKERS = ("@@bgm-mask@@", "@@/bgm-mask@@")

#: 主页链接 ``/user/<标识>`` —— ⚠️ 标识**两种形态都有**：老用户是数字 uid
#: （``/user/950407``），新用户是用户名 slug（``/user/eidosoma``）。
#: 只认数字的话，新用户的作者名与主页**会整个丢失**（实测踩到）。
_USER_LINK_RE = re.compile(r"/user/([^/?#]+)")


class BangumiError(Exception):
    """bgm.tv 解析失败"""


@dataclass
class BangumiImage:
    """日志正文里的上传图（表情不算）。"""

    url: str
    width: int = 0
    height: int = 0


@dataclass
class BangumiBlog:
    """一篇日志。"""

    blog_id: str = ""
    title: str = ""
    markdown_content: str = ""
    text_content: str = ""
    author_name: str = ""
    #: 数字 uid（bgm 没有 @用户名，主页与日志标签页都用 uid）
    author_handle: str = ""
    author_avatar: str = ""
    published_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    images: list[BangumiImage] = field(default_factory=list)

    @staticmethod
    def get_id_by_url(url: str) -> str:
        match = re.search(r"/blog/(\d+)", url)
        return match.group(1) if match else ""

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> BangumiBlog:
        blog_id = cls.get_id_by_url(url)
        if not blog_id:
            raise BangumiError(f"链接里没有日志 id: {url}")

        api = BLOG_API.format(blog_id=blog_id)
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(api)
        except http.HTTPError as e:
            raise BangumiError(f"请求 bgm.tv 失败: {e}") from e

        if response.status_code == 404:
            raise BangumiError(f"日志 {blog_id} 不存在")
        response.raise_for_status()

        # 显式 utf-8：站点不声明字符集，交给客户端猜会得到 latin-1（标题乱码）
        html = response.content.decode("utf-8", errors="replace")
        return cls._from_html(html, blog_id)

    @classmethod
    def _from_html(cls, html: str, blog_id: str) -> BangumiBlog:
        soup = BeautifulSoup(html, "lxml")

        entry = soup.find(id="entry_content")
        if entry is None:
            # 日志不存在/不可见时页面仍是 200，只有这句提示
            if _ERROR_MARKER in html:
                raise BangumiError(f"日志 {blog_id} 不存在或不可见")
            raise BangumiError(f"日志 {blog_id} 页面结构不认识（bgm.tv 可能改版了）")

        title_node = soup.select_one(".header h1.title")
        title = title_node.get_text(strip=True) if title_node else ""

        author_name, author_uid, avatar = cls._parse_author(soup)
        published_at = cls._parse_time(soup)
        tags = [t.get_text(strip=True) for t in soup.select(".header .tags .badge_tag") if t.get_text(strip=True)]

        images = _extract_images(entry)
        # 纯文本在**清理之后**取（清理会补 alt 文本、去掉图与样式外壳）——
        # 反过来取会把 markdown 标记或图片 alt 混进去
        _simplify(entry)
        text_content = entry.get_text("\n", strip=True)
        markdown_content = _to_markdown(str(entry))

        return cls(
            blog_id=blog_id,
            title=title,
            markdown_content=markdown_content,
            text_content=text_content,
            author_name=author_name,
            author_handle=author_uid,
            author_avatar=avatar,
            published_at=published_at,
            tags=tags,
            images=images,
        )

    # ------------------------------------------------------------------ 元信息

    @staticmethod
    def _parse_author(soup: BeautifulSoup) -> tuple[str, str, str]:
        """作者显示名 / 数字 uid / 头像。uid 从 ``/user/<uid>`` 链接里取。"""
        block = soup.select_one(".author")
        name = ""
        uid = ""
        avatar = ""
        if block:
            for a in block.find_all("a", href=True):
                if not (match := _USER_LINK_RE.search(a["href"])):
                    continue
                uid = uid or match.group(1)
                # 块里第一个 /user/ 链接是**头像**（里面只有 img，没有文字）——
                # 取到的名字会是空串，得挑那个有文字的（下一个才是显示名）
                if text := a.get_text(strip=True):
                    name = text
                    break
            img = block.find("img")
            if img is not None:
                avatar = _absolute_url(img.get("src"))
        return name, uid, avatar

    @staticmethod
    def _parse_time(soup: BeautifulSoup) -> datetime | None:
        """``2026-10-4 19:13 · 1 分钟阅读`` → 带时区的 datetime（阅读时长不进页脚）。

        ⚠️ 页面上的时间是**北京时间**（见 ``BGM_TIMEZONE``）—— 必须按 +08:00 解释，
        否则显示出来差 8 小时。
        """
        node = soup.select_one(".header .time")
        if node is None:
            return None
        text = re.split(r"[·|]", node.get_text(" ", strip=True))[0].strip()
        return to_datetime(text, default_tz=BGM_TIMEZONE)



# ------------------------------------------------------------------ 正文

def _extract_images(entry: Any) -> list[BangumiImage]:
    """正文里的**上传图**（表情不算）。判据看 **src 路径**，不能只看 class。

    表情是 ``/img/smiles/…``（alt 形如 ``(bgm116)``）—— 它是文字的一部分，
    不是要发的媒体；判错的话每条带表情的日志都会多出一堆 gif。

    图**从正文里摘掉**：统一走媒体通道发送（与 linux.do 同一形态）——
    留在正文里的外链图，Telegram 会自己去抓，抓不到就整张丢。
    """
    images: list[BangumiImage] = []
    seen: set[str] = set()
    for img in entry.find_all("img"):
        src = str(img.get("src") or "")
        cls = img.get("class") or []
        if "/img/smiles/" in src or "smile" in cls:
            continue
        url = _absolute_url(src)
        if not url or url in seen:
            continue
        seen.add(url)
        images.append(BangumiImage(url=url))
    return images

def _simplify(entry: Any) -> None:
    """把 bgm 特有的写法归一成标准标签，再交给 markdownify。

    分派依据是 **style**（`[u]/[s]/[mask]` 都是 span，只有 style 不同）：

    - ``text-decoration:underline`` → ``<u>``
    - ``text-decoration:line-through`` → ``<s>``
    - ``background-color`` → **马赛克** → 转成 Telegram 原生剧透 ``||…||``
    - ``font-weight:bold`` → ``<strong>``（编辑器出的写法，指南里没有）
    - ``color`` / ``font-size`` → 富文本没有颜色与字号 ⇒ **只保留文字**
    """
    # 表情 → 它的 alt 文本（用户定案：转成文本）
    for img in entry.find_all("img"):
        src = str(img.get("src") or "")
        cls = img.get("class") or []
        if "/img/smiles/" in src or "smile" in cls:
            img.replace_with(str(img.get("alt") or "").strip())
        else:
            img.decompose()

    for span in entry.find_all("span"):
        style = str(span.get("style") or "").lower().replace(" ", "")
        if "background-color" in style:
            # 马赛克：包一层标记，转完 markdown 再换成 ||…||（markdownify 认不出它）
            # 块内不能有换行 —— Telegram 的剧透是**行内**的，跨行会破掉
            for br in span.find_all("br"):
                br.replace_with(" ")
            span.insert_before(_MASK_MARKERS[0])
            span.insert_after(_MASK_MARKERS[1])
            span.unwrap()
        elif "text-decoration:underline" in style:
            span.name = "u"
            span.attrs = {}
        elif "text-decoration:line-through" in style:
            span.name = "s"
            span.attrs = {}
        elif "font-weight:bold" in style:
            span.name = "strong"
            span.attrs = {}
        else:
            # color / font-size / 其它：富文本表达不了，只留文字
            span.unwrap()

    # 引用块：`<div class="quote"><q>…</q></div>` → 标准 blockquote（markdownify 会加 `> `）
    for quote in entry.find_all("div", class_="quote"):
        quote.name = "blockquote"
        quote.attrs = {}
    for q in entry.find_all("q"):
        q.unwrap()

def _to_markdown(html: str) -> str:
    """HTML → markdown；马赛克标记换成 Telegram 剧透语法。"""
    if not html:
        return ""
    converter = _BgMarkdownConverter(heading_style="ATX")
    markdown_content = str(converter.convert(html))
    markdown_content = markdown_content.replace(_MASK_MARKERS[0], "||").replace(_MASK_MARKERS[1], "||")
    # markdownify 把 `<br/>` 转成"行尾两空格 + 换行"；连着两个 br 就留下**只有空格的行**，
    # 渲染出来是一行多余的空行。先把这种行清掉，再合并多余空行。
    markdown_content = re.sub(r"(?m)^[ \t]+$", "", markdown_content)
    markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
    return markdown_content.strip()


class _BgMarkdownConverter(MarkdownConverter):
    """锚点一律写成 **HTML `<a>`**。

    项目既有结论：markdown 链接在**引用块内不解析**（会原样显示成 ``[文字](url)``），
    而 HTML 写法两处都有效。bgm 的引用块（``div.quote``）里完全可能有链接。
    """

    def convert_a(self, el: Any, text: str, parent_tags: Any) -> str:
        href = str(el.get("href") or "").strip()
        if not href or not text:
            return text or ""
        return f'<a href="{html.escape(href, quote=True)}">{text}</a>'

    def convert_strong(self, el: Any, text: str, parent_tags: Any) -> str:
        """粗体一律写 HTML ``<b>``（真机验证生效: ``RichTextBold``）。

        ⚠️ **嵌套引用里的 markdown 星号会字面显示**：bgm 自己在楼中楼里插「某人 说: …」
        的引用（原文 ``<strong>``），markdownify 会把它转成 ``**名字**``；而读回的服务端块
        内容是 ``[ "**", {RichTextUrl…} ]`` —— 星号成了正文。

        （实测对照：**单层**引用块里的 ``**`` 是能渲染成 ``RichTextBold`` 的，
        出问题的是嵌套那一层。这里保守地一律用 HTML —— 段落与各种引用深度都生效。）
        """
        return f"<b>{text}</b>" if text else ""

    def convert_em(self, el: Any, text: str, parent_tags: Any) -> str:
        """斜体同上（``<i>``）—— 与 linux.do 引用块的作者行同一写法（已验证）。"""
        return f"<i>{text}</i>" if text else ""

    def convert_u(self, el: Any, text: str, parent_tags: Any) -> str:
        """下划线：markdown 没有这个语法（markdownify 会**整段丢掉** `<u>`）——
        富文本认 ``<u>``（真机实测回来是 RichTextUnderline），所以原样留着。
        """
        return f"<u>{text}</u>" if text else ""

    def convert_s(self, el: Any, text: str, parent_tags: Any) -> str:
        """删除线：默认会转成 ``~~…~~``；统一用 HTML 写法（真机实测 ``<s>`` → RichTextStrikethrough）"""
        return f"<s>{text}</s>" if text else ""



def _absolute_url(url: Any) -> str:
    """bgm 的图片是**协议相对**的（``//lain.bgm.tv/…``）—— 不补 https 会被当站内路径。"""
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    return text


#: 楼层号与时间在同一个 ``<small>`` 里：``#2 - 2026-10-7 00:24``
_FLOOR_HEAD_RE = re.compile(r"^(#\S+)\s*[-–—]\s*(.*)$")
#: 主楼是 ``.postTopic``，楼中楼是 ``.sub_reply_bg``（DOM 上**嵌在父楼内部**）
_FLOOR_BODY_SELECTOR = ".topic_content, .reply_content, .cmt_sub_content"


@dataclass
class BangumiFloor:
    """话题里的一层（含楼中楼）。"""

    dom_id: str = ""
    """节点的 ``id``（``post_<数字>`` 后面那串数字）—— URL 锚点用的就是它"""
    label: str = ""
    """楼层号，如 ``#2`` / ``#2-1``（bgm 自己的编号，楼中楼带 ``-``；与 dom_id 无关）"""
    author_name: str = ""
    author_handle: str = ""
    published_at: str = ""
    markdown: str = ""
    plain: str = ""
    """纯文本形态（纯图话题走 text_content 那条路要用）"""
    is_sub: bool = False
    """楼中楼（回复某个楼层，而不是回复主楼）"""
    parent_dom_id: str = ""
    """楼中楼**回复的那一层**（DOM 上最近的 ``post_<id>`` 祖先）；一级楼层为空。

    被回复对象是**父楼**而不是主楼 —— 分享楼中楼时引用块要用它（用户定案：
    「引用父楼（#4，它实际回复的那层）」）。
    """
    image_urls: list[str] = field(default_factory=list)
    """这一层自己的图片（用来切分引用块媒体）"""


@dataclass
class BangumiTopic:
    """bgm 的讨论话题（小组话题 ``/group/topic`` 或条目讨论版 ``/subject/topic``）。

    **只发一层**（与 linux.do 的楼层处理同一形态）：

    - 链接不带楼层锚点 → 发**主楼**
    - 链接带 ``#post_<id>`` 锚点 → 发**那一层**，并把主楼做成引用块当上下文
      （那一层常常是在回应主楼）

    以前是把主楼 + 全部楼层铺出来，长帖（45 层）整篇塞满消息 —— 用户要求改成只发一层。
    """

    topic_id: str = ""
    title: str = ""
    markdown_content: str = ""
    text_content: str = ""
    author_name: str = ""
    """**本层**的作者（不是楼主 —— 楼主是主题的创建者，楼层可能不是他）"""
    author_handle: str = ""
    published_at: datetime | None = None
    images: list[BangumiImage] = field(default_factory=list)
    #: ``images`` 末尾有多少张属于**引用块**（被引用/被回复的那层）——
    #: bot 侧据此把它们放进引用块内部（与 linux.do 同一机制）
    quoted_media_count: int = 0
    #: 引用块的角色（按出现顺序）—— 渲染层据此归位媒体, **不再看位置**
    quote_roles: list[str] = field(default_factory=list)
    floor_label: str = ""
    """本层的楼层号（``#1`` / ``#5`` / ``#2-1``）"""
    is_opening: bool = False
    """本层是不是主楼（主楼不给自己做引用块）"""
    context_name: str = ""
    """归属名：小组话题是小组名，条目讨论版是条目名"""
    context_url: str = ""
    floors: list[BangumiFloor] = field(default_factory=list)
    """解析出来的楼层（仅供核对/测试，**不再渲染**）"""

    @staticmethod
    def _topic_ref(url: str) -> tuple[str, str]:
        """从 URL 取 ``(归属, 话题 id)`` —— 归属是 ``group`` 或 ``subject``。

        **三种入口指向同一个话题**（实测 ``/rakuen/topic/subject/41119`` 与
        ``/subject/topic/41119`` 的正文逐块相同、楼层号一致）：

        - ``/group/topic/<id>``         小组话题
        - ``/subject/topic/<id>``       条目讨论版
        - ``/rakuen/topic/<归属>/<id>`` 「超展开」列表里的入口（页面更精简，但同构）
        """
        for pattern in (_RAKUEN_URL_RE, _TOPIC_URL_RE):
            if match := pattern.search(url or ""):
                return match.group(1), match.group(2)
        return "", ""

    @staticmethod
    def get_id_by_url(url: str) -> str:
        return BangumiTopic._topic_ref(url)[1]

    @staticmethod
    def get_floor_id_by_url(url: str) -> str:
        """URL 锚点里的楼层 id（``…#post_4062141`` → ``4062141``）；没有则空串。

        bgm 的楼层锚点就是楼层节点的 ``id``，所以拿到它就能定位那一层。
        """
        match = re.search(r"#post_(\d+)", url or "")
        return match.group(1) if match else ""

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> BangumiTopic:
        kind, topic_id = cls._topic_ref(url)
        if not topic_id:
            raise BangumiError(f"链接里没有话题 id: {url}")
        # rakuen 的入口归一化到**规范路径**再抓：它的页面更精简（16KB vs 26KB），
        # 归属链指向条目而不是小组，header 分支与规范页面也不同 —— 归一化后
        # 复用已验证的那两条路径，零新增解析逻辑。
        api = _TOPIC_APIS[kind].format(topic_id=topic_id)
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(api)
        except http.HTTPError as e:
            raise BangumiError(f"请求 bgm.tv 失败: {e}") from e
        if response.status_code == 404:
            raise BangumiError(f"话题 {topic_id} 不存在")
        response.raise_for_status()
        html = response.content.decode("utf-8", errors="replace")
        return cls._from_html(html, topic_id, floor_id=cls.get_floor_id_by_url(url))

    @classmethod
    def _from_html(cls, html: str, topic_id: str, *, floor_id: str = "") -> BangumiTopic:
        soup = BeautifulSoup(html, "lxml")

        main_post = soup.select_one(".postTopic")
        if main_post is None:
            if _ERROR_MARKER in html:
                raise BangumiError(f"小组话题 {topic_id} 不存在或不可见")
            raise BangumiError(f"小组话题 {topic_id} 页面结构不认识（bgm.tv 可能改版了）")

        title, context_name, context_url = cls._parse_header(soup)

        # ⚠️ **图片先抽**：解析楼层时会 `decompose()` 掉嵌在父楼正文里的楼中楼节点，
        # 那之后楼中楼里的图就找不到了（它们在树上已经不存在）。
        images: list[BangumiImage] = []
        seen: set[str] = set()
        for node in soup.select(_FLOOR_BODY_SELECTOR):
            for img in _extract_images(node):
                if img.url not in seen:
                    seen.add(img.url)
                    images.append(img)

        all_floors = cls._collect_floors(soup, main_post)
        opening = next((f for f in all_floors if f.label == "#1"), None)
        if opening is None and all_floors:
            opening = all_floors[0]

        # **只发一层**：锚点指定的那层，没有锚点就是主楼
        current = cls._pick_floor(all_floors, opening, floor_id)
        return cls._build(topic_id, title, context_name, context_url, opening, current, all_floors, images)

    @staticmethod
    def _pick_floor(floors: list[BangumiFloor], opening: BangumiFloor | None, floor_id: str) -> BangumiFloor | None:
        """选要发的那一层。

        锚点指向的节点就是楼层节点（``id="post_<数字>"``）—— 楼层解析时把 ``node.get("id")``
        记进了 ``floor.dom_id``，所以这里按 id 找，**不按楼层号**（楼中楼的 ``#2-1``
        是 bgm 自己编的，与节点 id 无关）。

        锚点找不到（楼层被删 / 链接手改过）时**退回主楼** —— 宁可少一层上下文，
        也不能因为一个坏锚点整条解析失败。
        """
        if floor_id:
            for floor in floors:
                if floor.dom_id == floor_id:
                    return floor
            logger.warning(f"链接锚点 #post_{floor_id} 在页面上不存在, 改为发送主楼")
        return opening

    @classmethod
    def _build(
        cls,
        topic_id: str,
        title: str,
        context_name: str,
        context_url: str,
        opening: BangumiFloor | None,
        current: BangumiFloor | None,
        all_floors: list[BangumiFloor],
        images: list[BangumiImage],
    ) -> BangumiTopic:
        """组装结果：本层正文（+ 不是主楼时把主楼做成引用块，与 linux.do 同一形态）。"""
        is_opening = current is opening
        body = current.markdown if current else ""
        plain = current.plain if current else ""

        # 引用谁当上下文：**楼中楼引用父楼**（它回复的那层），其余引用主楼。
        # 用户定案「引用父楼（#4，它实际回复的那层）」—— 楼中楼在 DOM 上嵌在父楼里，
        # 拿主楼当上下文等于答非所问（#4-1 回的明明是 #4 的话）。
        context_floor = opening
        if current is not None and current.is_sub and current.parent_dom_id:
            parent = next((f for f in all_floors if f.dom_id == current.parent_dom_id), None)
            if parent is None:
                logger.warning(f"楼中楼 {current.label} 的父楼 {current.parent_dom_id} 不在页面上, 退回主楼当上下文")
            context_floor = parent or opening

        context_quote = ""
        quoted_media = 0
        if not is_opening and context_floor is not None:
            context_quote = cls._quote_of(context_floor)
            # 主楼的图走**引用块媒体**通道 —— 而那个通道的约定是「**末尾** N 张属于引用块」
            # （见 ParseResult.quoted_media_count）。图片是按 DOM 顺序抽的，主楼在最前面，
            # 所以这里要**重排**：本层的图在前、主楼的图挪到末尾。
            context_urls = set(context_floor.image_urls)
            images = [i for i in images if i.url not in context_urls] + [
                i for i in images if i.url in context_urls
            ]
            quoted_media = len([i for i in images if i.url in context_urls])

        markdown_content = cls._compose(context_name, context_url, context_quote, body)
        text_content = cls._to_text(context_name, context_quote, plain)

        return BangumiTopic(
            topic_id=topic_id,
            title=title,
            markdown_content=markdown_content,
            text_content=text_content,
            author_name=current.author_name if current else "",
            author_handle=current.author_handle if current else "",
            # 页面上的时间是北京时间 —— 按 +08:00 解释才能对上（见 BGM_TIMEZONE）
            published_at=to_datetime(current.published_at, default_tz=BGM_TIMEZONE) if current else None,
            images=images,
            quoted_media_count=quoted_media,
            # 引用块存在 ⇒ 角色是 ``quoted``（它那条媒体计数通道）
            quote_roles=["quoted"] if context_quote else [],
            floor_label=current.label if current else "",
            is_opening=is_opening,
            context_name=context_name,
            context_url=context_url,
            floors=[f for f in all_floors if f is not opening],
        )

    # ------------------------------------------------------------------ 解析

    @staticmethod
    def _parse_header(soup: BeautifulSoup) -> tuple[str, str, str]:
        """取 (标题, 归属名, 归属链接)。**两个页面形态不同**：

        - **小组话题**：``#pageHeader h1`` 里是「小组 » 讨论<br/>标题」—— 标题在 ``<br/>`` 之后
        - **条目讨论版**：页面上有**两个 h1** —— ``#headerSubject`` 里那个是**条目名**，
          真正的标题在 ``.comment-header h1``。归属是条目（``/subject/<id>``）。
        """
        # 小组话题
        node = soup.select_one("#pageHeader h1")
        if node is not None:
            name = ""
            url = ""
            for a in node.find_all("a", href=True):
                if (match := re.search(r"/group/([^/?]+)", a["href"])) and "/forum" not in a["href"]:
                    url = f"https://bgm.tv/group/{match.group(1)}"
                    name = a.get_text(strip=True)
                    break
            # 标题：取 ``<br/>`` 之后的文本；没有 br 时退化为整块文本
            br = node.find("br")
            title = (
                " ".join(str(s) for s in br.next_siblings).strip() if br is not None else node.get_text(" ", strip=True)
            )
            return re.sub(r"\s+", " ", title), name, url

        # 条目讨论版：标题在 .comment-header 里；条目名与链接在 #headerSubject
        title_node = soup.select_one(".comment-header h1")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        subject_node = soup.select_one("#headerSubject a[href^='/subject/']")
        if subject_node is not None:
            name = subject_node.get_text(" ", strip=True)
            url = f"https://bgm.tv{subject_node['href']}"
            return re.sub(r"\s+", " ", title), name, url
        return re.sub(r"\s+", " ", title), "", ""

    @classmethod
    def _collect_floors(cls, soup: BeautifulSoup, main_post: Any) -> list[BangumiFloor]:
        """按页面顺序收集楼层：主楼 → 一级回复（含各自的楼中楼）。

        楼中楼**在 DOM 上嵌在父楼的正文容器里**（``div.topic_reply_<父id>``），
        所以取父楼正文时要先把嵌套的子楼层摘掉，否则两段文字会糊在一起。

        ⚠️ **必须先收集全部节点、再逆序解析**：摘掉子楼层用的是 ``decompose()``，
        它会**清空被摘节点的内容**。正序解析时父楼先把子楼清掉，轮到子楼就只剩空壳
        （实测：45 个节点只解析出 38 条，7 条楼中楼全空）。逆序（子 → 父）就没有这个问题。
        """
        # (节点, 是否楼中楼, 楼中楼所回复的那一层的 dom id)
        nodes: list[tuple[Any, bool, str]] = [(main_post, False, "")]
        # 主楼**可能也出现在 comment_list 里**（页面结构变动时会出现；抽 fixture 时我
        # 就踩到过）—— 不去重的话主楼会进两次，第一层被当正文、第二层又当楼层出现。
        seen_ids = {main_post.get("id")}
        comment_list = soup.find(id="comment_list")
        if comment_list is not None:
            for node in comment_list.find_all(id=re.compile(r"^post_\d+$"), recursive=False):
                if node.get("id") in seen_ids:
                    continue
                seen_ids.add(node.get("id"))
                nodes.append((node, False, ""))
                # 楼中楼嵌在这层的正文容器里 ⇒ 它的父楼就是当前这层
                # （实测 `post_410747` 在 `topic_reply_410733` 内，即 #4-1 回复 #4）
                parent_dom_id = str(node.get("id") or "").removeprefix("post_")
                for sub in node.select("div.sub_reply_bg"):
                    if sub.get("id") in seen_ids:
                        continue
                    seen_ids.add(sub.get("id"))
                    nodes.append((sub, True, parent_dom_id))

        floors = [
            cls._parse_floor(node, is_sub=is_sub, parent_dom_id=parent_dom_id)
            for node, is_sub, parent_dom_id in reversed(nodes)
        ]
        floors.reverse()
        return floors

    @classmethod
    def _parse_floor(cls, node: Any, *, is_sub: bool, parent_dom_id: str = "") -> BangumiFloor:
        small = node.select_one(".post_actions small")
        label = ""
        published_at = ""
        if small is not None:
            match = _FLOOR_HEAD_RE.match(small.get_text(" ", strip=True))
            if match:
                label, published_at = match.group(1), match.group(2).strip()

        author_name = ""
        author_handle = ""
        for a in node.select("a[href^='/user/']"):
            text = a.get_text(strip=True)
            if not text:
                continue
            author_name = text
            author_handle = str(a["href"]).rsplit("/", 1)[-1]
            break

        dom_id = str(node.get("id") or "").removeprefix("post_")
        body = node.select_one(_FLOOR_BODY_SELECTOR)
        markdown = ""
        plain = ""
        image_urls: list[str] = []
        if body is not None:
            # 摘掉嵌套的楼中楼（它们会单独成条），只留这一层自己的文字
            for nested in body.find_all(id=re.compile(r"^post_\d+$")):
                nested.decompose()
            for aside in body.find_all("div", class_="topic_reply"):
                aside.decompose()
            for action in body.find_all("div", class_="post_actions"):
                action.decompose()
            # 图片要在 `_simplify` 之前取（它会 `decompose()` 掉图）
            image_urls = [i.url for i in _extract_images(body)]
            plain = body.get_text("\n", strip=True)
            _simplify(body)
            markdown = _to_markdown(str(body))

        return BangumiFloor(
            dom_id=dom_id,
            label=label,
            author_name=author_name,
            author_handle=author_handle,
            published_at=published_at,
            markdown=markdown,
            is_sub=is_sub,
            plain=plain,
            image_urls=image_urls,
            parent_dom_id=parent_dom_id,
        )

    # ------------------------------------------------------------------ 组装

    @classmethod
    def _compose(cls, context_name: str, context_url: str, context_quote: str, body: str) -> str:
        """**引用块 + 归属行 + 本层正文** —— 引用块放**最前**。

        形态与 linux.do 的楼层解析一致（上下文引用块在前、本层内容在后）。

        ⚠️ **引用块必须是最前面那一块**：渲染层对「**末尾**引用块」的处理是推特语义
        （主推的媒体排在引用块上面），媒体会被插到引用块**前面**、贴在一起
        （用户报「图片和引用贴一起了」）。放在开头走的是「被回复的卡片」那条通道
        （``reply_quote``），媒体自然跟在它后面。

        以前归属行在引用块之前，于是无正文的纯图楼层里引用块落到了**末尾** ——
        正好踩中上面那个坑。
        """
        parts: list[str] = []
        if context_quote:
            parts.append(context_quote)
        if context_name:
            label = (
                f'<a href="{html.escape(context_url, quote=True)}">{html.escape(context_name)}</a>'
                if context_url
                else html.escape(context_name)
            )
            parts.append(f"**{label}** » 讨论")
        if body:
            parts.append(body)
        # strip 每个块: ``format_quote_block`` 末尾自带空行, 直接 join 会堆出多余空行
        # —— 引用块与正文之间会变成 3 个空行, 行间距比别的平台大（用户报「感觉比其他
        # 平台的大一点」）。linux.do 早就这么处理了（见它的 `_context_quotes` 调用点）。
        return "\n\n".join(p.strip() for p in parts if p and p.strip())

    @staticmethod
    def _quote_of(floor: BangumiFloor) -> str:
        """把主楼渲染成引用块（分享楼层时当上下文用）。

        **走公共 helper ``format_quote_block``**（与其他平台同一形态：作者行在前 +
        整块斜体）。⚠️ 别在这里手拼 ``"> "`` 前缀 —— 手写的版本会漏掉形态细节
        （bgm 的引用块就是因为手写而漏了斜体，与其他平台不一致），
        公共 helper 才是唯一权威。
        """
        author = format_author_link(
            floor.author_name,
            floor.author_handle,
            profile_url(Platform.BANGUMI, user_id=floor.author_handle),
        )
        meta = " · ".join(x for x in (floor.label, floor.published_at) if x)
        if author and meta:
            author = f"{author} · {meta}"
        return format_quote_block(floor.markdown, author)

    @classmethod
    def _to_text(cls, context_name: str, context_quote: str, plain: str) -> str:
        """纯文本兜底（纯图话题那条路要用）。"""
        lines = [context_name] if context_name else []
        lines.extend(re.sub(r"^>\s?", "", ln).strip() for ln in context_quote.splitlines())
        if plain:
            lines.append(plain)
        return "\n".join(x for x in lines if x)


__all__ = [
    "BLOG_API",
    "GROUP_TOPIC_API",
    "SUBJECT_TOPIC_API",
    "BangumiBlog",
    "BangumiError",
    "BangumiFloor",
    "BangumiImage",
    "BangumiTopic",
]
