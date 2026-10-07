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
from typing import Any

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from ..types.platform import Platform
from ..utils import http
from ..utils.helpers import format_author_link, profile_url

BLOG_API = "https://bgm.tv/blog/{blog_id}"
GROUP_TOPIC_API = "https://bgm.tv/group/topic/{topic_id}"

#: 楼层太多时只取前 N 层 —— 几百层的长帖整篇塞进一条消息没有意义，
#: 而且真的会撞到消息体积上限。截断时在文末注明还剩多少层。
MAX_FLOORS = 60

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
    published_at: str = ""
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
    def _parse_time(soup: BeautifulSoup) -> str:
        """``2026-10-4 19:13 · 1 分钟阅读`` → ``2026-10-4 19:13``（阅读时长不进页脚）。"""
        node = soup.select_one(".header .time")
        if node is None:
            return ""
        return re.split(r"[·|]", node.get_text(" ", strip=True))[0].strip()



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
        """粗体一律写 HTML ``<b>``。

        ⚠️ 默认的 markdown ``**`` 在**引用块内不解析**（会字面显示星号）。bgm 自己就会
        在楼中楼里插「某人 说: …」的嵌套引用（原文是 ``<strong>``），那条也在引用块里 ——
        实测读回的服务端块内容是 ``[ "**", {RichTextUrl…} ]``，星号成了正文。
        HTML 写法在段落与引用块内都生效。
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


def _author_markup(label: str, *, tag: str, bold_only_name: bool = False) -> str:
    """给作者行加**强调**标记。

    - ``bold_only_name=True`` → 用 markdown ``**`` 且**只包名字**（与 bot 侧
      ``format_author_line`` 同一形态：整行包起来会让 ``@handle`` 也继承粗体）
    - 否则 → 用 HTML 标签包整行（引用块内 markdown 不解析，只能走 HTML）
    """
    if not label:
        return ""
    if bold_only_name:
        if "</a>" not in label:
            return f"**{label}**"
        head, _, tail = label.partition("</a>")
        return f"**{head}</a>**{tail}"
    return f"<{tag}>{label}</{tag}>"


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
    """小组话题里的一层（含楼中楼）。"""

    label: str = ""
    """楼层号，如 ``#2`` / ``#2-1``（bgm 自己的编号，楼中楼带 ``-``）"""
    author_name: str = ""
    author_handle: str = ""
    published_at: str = ""
    markdown: str = ""
    plain: str = ""
    """纯文本形态（纯图话题走 text_content 那条路要用）"""
    is_sub: bool = False
    """楼中楼（回复某个楼层，而不是回复主楼）"""


@dataclass
class BangumiGroupTopic:
    """一个小组话题（主楼 + 楼层）。"""

    topic_id: str = ""
    title: str = ""
    group_name: str = ""
    group_url: str = ""
    markdown_content: str = ""
    text_content: str = ""
    author_name: str = ""
    author_handle: str = ""
    published_at: str = ""
    images: list[BangumiImage] = field(default_factory=list)
    floors: list[BangumiFloor] = field(default_factory=list)
    total_floors: int = 0
    """页面上的楼层总数（含被截断的）"""

    @staticmethod
    def get_id_by_url(url: str) -> str:
        match = re.search(r"/group/topic/(\d+)", url)
        return match.group(1) if match else ""

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> BangumiGroupTopic:
        topic_id = cls.get_id_by_url(url)
        if not topic_id:
            raise BangumiError(f"链接里没有小组话题 id: {url}")
        api = GROUP_TOPIC_API.format(topic_id=topic_id)
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(api)
        except http.HTTPError as e:
            raise BangumiError(f"请求 bgm.tv 失败: {e}") from e
        if response.status_code == 404:
            raise BangumiError(f"小组话题 {topic_id} 不存在")
        response.raise_for_status()
        html = response.content.decode("utf-8", errors="replace")
        return cls._from_html(html, topic_id)

    @classmethod
    def _from_html(cls, html: str, topic_id: str) -> BangumiGroupTopic:
        soup = BeautifulSoup(html, "lxml")

        main_post = soup.select_one(".postTopic")
        if main_post is None:
            if _ERROR_MARKER in html:
                raise BangumiError(f"小组话题 {topic_id} 不存在或不可见")
            raise BangumiError(f"小组话题 {topic_id} 页面结构不认识（bgm.tv 可能改版了）")

        title, group_name, group_url = cls._parse_header(soup)

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
        # 主楼（#1）单独拿出来当正文，其余作为楼层
        main_floor = next((f for f in all_floors if f.label == "#1"), None)
        if main_floor is None and all_floors:
            main_floor = all_floors[0]
        floors = [f for f in all_floors if f is not main_floor]

        author_name = main_floor.author_name if main_floor else ""
        author_handle = main_floor.author_handle if main_floor else ""
        published_at = main_floor.published_at if main_floor else ""
        body_markdown = main_floor.markdown if main_floor else ""
        body_text = main_floor.plain if main_floor else ""

        markdown_content = cls._compose(group_name, group_url, len(all_floors), body_markdown, floors)
        text_content = cls._to_text(group_name, body_text, floors)

        return cls(
            topic_id=topic_id,
            title=title,
            group_name=group_name,
            group_url=group_url,
            markdown_content=markdown_content,
            text_content=text_content,
            author_name=author_name,
            author_handle=author_handle,
            published_at=published_at,
            images=images,
            floors=floors,
            total_floors=len(all_floors),
        )

    # ------------------------------------------------------------------ 解析

    @staticmethod
    def _parse_header(soup: BeautifulSoup) -> tuple[str, str, str]:
        """``<h1>`` 里是「小组 » 讨论<br/>标题」—— 标题在 ``<br/>`` 之后。"""
        node = soup.select_one("#pageHeader h1") or soup.select_one("h1")
        if node is None:
            return "", "", ""
        group_name = ""
        group_url = ""
        for a in node.find_all("a", href=True):
            if (match := re.search(r"/group/([^/?]+)", a["href"])) and "/forum" not in a["href"]:
                group_url = f"https://bgm.tv/group/{match.group(1)}"
                group_name = a.get_text(strip=True)
                break
        # 标题：取 ``<br/>`` 之后的文本；没有 br 时退化为整块文本
        br = node.find("br")
        if br is not None:
            title = " ".join(str(s) for s in br.next_siblings).strip()
        else:
            title = node.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", title), group_name, group_url

    @classmethod
    def _collect_floors(cls, soup: BeautifulSoup, main_post: Any) -> list[BangumiFloor]:
        """按页面顺序收集楼层：主楼 → 一级回复（含各自的楼中楼）。

        楼中楼**在 DOM 上嵌在父楼的正文容器里**（``div.topic_reply_<父id>``），
        所以取父楼正文时要先把嵌套的子楼层摘掉，否则两段文字会糊在一起。

        ⚠️ **必须先收集全部节点、再逆序解析**：摘掉子楼层用的是 ``decompose()``，
        它会**清空被摘节点的内容**。正序解析时父楼先把子楼清掉，轮到子楼就只剩空壳
        （实测：45 个节点只解析出 38 条，7 条楼中楼全空）。逆序（子 → 父）就没有这个问题。
        """
        nodes: list[tuple[Any, bool]] = [(main_post, False)]
        # 主楼**可能也出现在 comment_list 里**（页面结构变动时会出现；抽 fixture 时我
        # 就踩到过）—— 不去重的话主楼会进两次，第一层被当正文、第二层又当楼层出现。
        seen_ids = {main_post.get("id")}
        comment_list = soup.find(id="comment_list")
        if comment_list is not None:
            for node in comment_list.find_all(id=re.compile(r"^post_\d+$"), recursive=False):
                if node.get("id") in seen_ids:
                    continue
                seen_ids.add(node.get("id"))
                nodes.append((node, False))
                for sub in node.select("div.sub_reply_bg"):
                    if sub.get("id") in seen_ids:
                        continue
                    seen_ids.add(sub.get("id"))
                    nodes.append((sub, True))

        floors = [cls._parse_floor(node, is_sub=is_sub) for node, is_sub in reversed(nodes)]
        floors.reverse()
        return floors

    @classmethod
    def _parse_floor(cls, node: Any, *, is_sub: bool) -> BangumiFloor:
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

        body = node.select_one(_FLOOR_BODY_SELECTOR)
        markdown = ""
        plain = ""
        if body is not None:
            # 摘掉嵌套的楼中楼（它们会单独成条），只留这一层自己的文字
            for nested in body.find_all(id=re.compile(r"^post_\d+$")):
                nested.decompose()
            for aside in body.find_all("div", class_="topic_reply"):
                aside.decompose()
            for action in body.find_all("div", class_="post_actions"):
                action.decompose()
            plain = body.get_text("\n", strip=True)
            _simplify(body)
            markdown = _to_markdown(str(body))

        return BangumiFloor(
            label=label,
            author_name=author_name,
            author_handle=author_handle,
            published_at=published_at,
            markdown=markdown,
            is_sub=is_sub,
            plain=plain,
        )

    # ------------------------------------------------------------------ 组装

    @classmethod
    def _compose(
        cls, group_name: str, group_url: str, total: int, body: str, floors: list[BangumiFloor]
    ) -> str:
        """主楼正文 + 楼层区。

        形态（与项目其它平台同一套「还原原文排版」的取向）：

        - 小组归属单独一行（读者一眼知道出处）；楼层数一并在那里说明
        - 主楼正文照原样
        - 主楼与讨论之间一条分割线（区分「话题」与「大家怎么回」）
        - 每层：作者行（`` · #N · 时间``）+ 正文，**同一段**（楼层头是引子）
        - **楼中楼用引用块**（``> ``）表达层级 —— 与 linux.do 的楼层上下文同形
        """
        parts: list[str] = []
        if group_name:
            label = (
                f'<a href="{html.escape(group_url, quote=True)}">{html.escape(group_name)}</a>'
                if group_url
                else html.escape(group_name)
            )
            parts.append(f"**{label}** » 讨论 · {total} 层")
        if body:
            parts.append(body)

        shown = floors[:MAX_FLOORS]
        if shown:
            block: list[str] = ["---"]
            for floor in shown:
                block.append(cls._floor_markdown(floor))
            if len(floors) > len(shown):
                block.append(f"（还有 {len(floors) - len(shown)} 层未显示）")
            parts.append("\n\n".join(block))
        return "\n\n".join(p for p in parts if p).strip()

    @classmethod
    def _floor_markdown(cls, floor: BangumiFloor) -> str:
        link = format_author_link(
            floor.author_name,
            floor.author_handle,
            profile_url(Platform.BANGUMI, user_id=floor.author_handle),
        )
        meta = " · ".join(x for x in (floor.label, floor.published_at) if x)

        if floor.is_sub:
            # 楼中楼：引用块表达层级（与 linux.do 的处理一致）。
            # ⚠️ **引用块内 markdown 不解析** —— 写 `**粗体**` 会**字面显示两个星号**
            # （实测块内容里出现孤立的 `"**"`）。所以这里用 HTML `<i>`，
            # 与 linux.do 引用块的作者行同一写法（那条路已验证生效）。
            line = _author_markup(link, tag="i")
            head = f"{line} · {meta}" if meta else line
            lines = [f"> {head}"]
            body = floor.markdown.strip()
            if body:
                lines.extend(f"> {ln}" if ln.strip() else ">" for ln in body.splitlines())
            return "\n".join(lines)

        # 一级楼层在普通段落里，markdown 生效，用粗体（只包名字）
        line = _author_markup(link, tag="strong", bold_only_name=True)
        head = f"{line} · {meta}" if meta else line
        body = floor.markdown.strip()
        if not body:
            return head
        # 楼层头与正文分两段，读起来才分得清谁说的
        return f"{head}\n\n{body}"

    @classmethod
    def _to_text(cls, group_name: str, body: str, floors: list[BangumiFloor]) -> str:
        """纯文本兜底（纯图话题那条路要用）。"""
        lines = [group_name] if group_name else []
        if body:
            lines.append(body)
        for floor in floors[:MAX_FLOORS]:
            lines.append(f"{floor.author_name} {floor.label} {floor.plain}".strip())
        return "\n".join(x for x in lines if x)



__all__ = [
    "BLOG_API",
    "GROUP_TOPIC_API",
    "MAX_FLOORS",
    "BangumiBlog",
    "BangumiError",
    "BangumiFloor",
    "BangumiGroupTopic",
    "BangumiImage",
]
