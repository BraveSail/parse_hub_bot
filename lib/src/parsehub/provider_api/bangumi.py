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

from ..utils import http

BLOG_API = "https://bgm.tv/blog/{blog_id}"

#: 页面里日志不存在时**仍然返回 HTTP 200**（不是 404），正文写「呜咕，出错了 数据库中没有…」
#: ⇒ 判据只能是「没有 entry_content」，不能看状态码。
_ERROR_MARKER = "数据库中没有查询到该日志的信息"

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

        images = cls._extract_images(entry)
        # 纯文本在**清理之后**取（清理会补 alt 文本、去掉图与样式外壳）——
        # 反过来取会把 markdown 标记或图片 alt 混进去
        cls._simplify(entry)
        text_content = entry.get_text("\n", strip=True)
        markdown_content = cls._to_markdown(str(entry))

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

    @staticmethod
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

    @classmethod
    def _simplify(cls, entry: Any) -> None:
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

    @staticmethod
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


__all__ = ["BLOG_API", "BangumiBlog", "BangumiError", "BangumiImage"]
