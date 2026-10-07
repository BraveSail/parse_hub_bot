"""linux.do（Discourse 论坛）解析。

数据走 Discourse 自带的 JSON 接口: ``/t/topic/<id>.json``（比抓 HTML 稳）。

⚠️ 该站点前置 Cloudflare，**必须同时满足两点**才能拿到数据（2026-10 实测）：

1. **浏览器 TLS 指纹** —— 本项目统一走 ``parsehub.utils.http``（curl_cffi, chrome150）。
   实测同一份 cookie 下 httpx 得到 403 挑战页、curl_cffi 得到 200。
2. **携带 cookie** —— 至少要有 ``cf_clearance``（Cloudflare 放行票）与 ``_forum_session``
   （Discourse 会话）。请在 ``platform_config.yaml`` 的 ``platforms.linuxdo.cookies`` 里配置
   浏览器里复制的完整 Cookie 头；``cf_clearance`` 会过期，失效后表现为 403。

不需要 cookie 的接口不存在 —— 匿名访问同样被 Cloudflare 拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from ..types.platform import Platform
from ..utils import http
from ..utils.helpers import format_author_link, format_quote_block, profile_url, to_int

TOPIC_API = "https://linux.do/t/topic/{topic_id}.json"
#: 带楼层号: Discourse 返回以该楼层为中心的窗口
FLOOR_API = "https://linux.do/t/{topic_id}/{post_number}.json"

#: /t/topic/<id>[/<楼层>]、/t/<slug>/<id>[/<楼层>]、/t/<id>[/<楼层>]
#: slug 不以数字开头, 否则 /t/2979226/11 会被误当成 slug=2979226、id=11
_TOPIC_URL_RE = re.compile(r"^https?://linux\.do/t/(?:[^\d/][^/]*/)?(\d+)(?:/(\d+))?")


class LinuxDoError(Exception):
    """linux.do 解析失败。"""


@dataclass
class LinuxDoImage:
    """帖子里的图片。"""

    url: str
    width: int = 0
    height: int = 0


@dataclass
class LinuxDoTopic:
    """一个主题（楼主帖 + 主题级统计）。"""

    topic_id: str = ""
    title: str = ""
    markdown_content: str = ""
    text_content: str = ""
    author_name: str = ""
    author_handle: str = ""
    published_at: str = ""
    view_count: int | None = None
    like_count: int | None = None
    reply_count: int | None = None
    tags: list[str] = field(default_factory=list)
    images: list[LinuxDoImage] = field(default_factory=list)
    #: images 末尾有多少张属于**上下文引用块** (主楼/被回复楼层), bot 侧据此把它们
    #: 放进引用块内部而不是正文媒体
    quoted_media_count: int = 0
    is_sensitive: bool = False
    #: **当前解析的这一层的楼层号** (主楼是 1)。渲染层用它给作者行标 ``#N`` ——
    #: 引用块里的其它层早就标了 (`_post_to_quote` 的 `` · #N``), 本层不标就不对称。
    post_number: int | None = None

    @staticmethod
    def _split_url(url: str) -> tuple[str, str]:
        """返回 (话题 ID, 楼层号)；楼层号为空串表示没指定。"""
        if match := _TOPIC_URL_RE.match(url):
            return match.group(1), match.group(2) or ""
        raise LinuxDoError("暂不支持该 linux.do 链接, 目前仅支持话题页 (/t/<话题>/<id>)")

    @staticmethod
    def get_topic_id(url: str) -> str:
        return LinuxDoTopic._split_url(url)[0]

    @staticmethod
    def get_post_number(url: str) -> str:
        """URL 里指定的楼层号（``/t/topic/<id>/11`` → ``11``）；没指定返回空串。

        带楼层号时解析那一层（用户分享的常常是某个回复），不带则解析楼主帖。
        """
        return LinuxDoTopic._split_url(url)[1]

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> LinuxDoTopic:
        topic_id, post_number = cls._split_url(url)
        # 带楼层号时请求该楼层（Discourse 会返回以它为中心的窗口），否则请求楼主帖
        api = FLOOR_API.format(topic_id=topic_id, post_number=post_number) if post_number else TOPIC_API.format(
            topic_id=topic_id
        )
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(api, headers={"Accept": "application/json"})
        except http.HTTPError as e:
            raise LinuxDoError(f"请求 linux.do 失败: {e}") from e

        if response.status_code == 403:
            raise LinuxDoError("linux.do 需要有效的 cookie (含 cf_clearance), 请在平台配置里更新")
        if response.status_code == 404:
            raise LinuxDoError(f"话题 {topic_id} 不存在或不可见")
        response.raise_for_status()

        try:
            payload: dict[str, Any] = response.json()
        except Exception as e:  # noqa: BLE001 - 被 Cloudflare 拦时会返回 HTML
            raise LinuxDoError("linux.do 返回的不是 JSON, 多半是 Cloudflare 挑战页面 (cookie 失效或缺少指纹)") from e

        return cls._from_payload(payload, topic_id, post_number=post_number)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], topic_id: str, post_number: str = "") -> LinuxDoTopic:
        """组装主题。

        ``post_number`` 指定楼层时取那一层（用户分享的链接常带楼层号，那一层才是他要看的内容）；
        没指定则取楼主帖。带楼层号的 URL 返回的帖子流**不含 1 楼**，所以不能盲目回落到第一项 ——
        那会取到窗口里的其它楼层（症状：明明楼层里有图，解析出来没媒体）。
        """
        posts: list[dict[str, Any]] = (payload.get("post_stream") or {}).get("posts") or []
        wanted = int(post_number) if post_number.isdigit() else 1
        first = next((p for p in posts if p.get("post_number") == wanted), None)
        if first is None and post_number:
            raise LinuxDoError(f"话题 {topic_id} 里没有第 {post_number} 楼")
        if first is None:
            first = posts[0] if posts else None
        if first is None:
            raise LinuxDoError("话题里没有可解析的内容")

        cooked: str = first.get("cooked") or ""
        soup = BeautifulSoup(cooked, "lxml")
        # 投票: cooked 里的 poll 只是个**占位壳**（人数恒为 0），真实数据在同一次响应的
        # ``post["polls"]`` 结构化字段里（`options[].votes` / `voters`）—— 不需要额外请求。
        polls = [p for p in (first.get("polls") or []) if isinstance(p, dict)]
        images = cls._extract_images(soup)
        # ⚠️ 必须在取纯文本**之前**清 lightbox: 它的包裹里除了 img 还有一段**说明文字**
        # (``span.filename`` 文件名 + ``span.informations`` 的"分辨率 体积", 如 ``746×330 24.3 KB``)。
        # 纯图楼层(markdown 为空)拿 text_content 当正文, 晚清理这些元信息就会跟图一起显示
        # (用户报「正文多了个 image 分辨率体积」)。
        cls._strip_lightbox(soup)
        text_content = soup.get_text("\n", strip=True)
        # 引用块先抽出来 (改写成占位符), 转完 markdown 再回填公共 helper 渲染的结果
        rendered_quotes = cls._extract_quotes(soup)
        cls._simplify(soup, polls=polls)
        markdown_content = cls._to_markdown(str(soup))
        for placeholder, block in rendered_quotes:
            markdown_content = markdown_content.replace(placeholder, block)

        # 上下文引用块放正文**之前**, 从远到近: 主楼 → 被回复的楼层 → 当前楼层正文
        # (用户要求: 分享楼层时把主楼做成回复; 楼层互回时被回复的那层也要带上)
        context_quotes, context_images = cls._context_quotes(posts, first)
        # 上下文层的图片接在 media 末尾 —— bot 侧按"引用块媒体"放进引用块内部
        quoted_media_count = len(context_images)
        if context_images:
            images = [*images, *context_images]
        if context_quotes:
            # strip 每个块: format_quote_block 末尾自带空行, 直接 join 会堆出多余空行
            parts = [*[q.strip() for q in context_quotes], markdown_content.strip()]
            markdown_content = "\n\n".join(p for p in parts if p)
            # 纯图楼层走 text_content 兜底, 上下文同样要带上
            plain_parts = [*[cls._quote_to_plain(q) for q in context_quotes], text_content.strip()]
            text_content = "\n\n".join(p for p in plain_parts if p)

        tags = [str(t.get("name")) for t in (payload.get("tags") or []) if isinstance(t, dict) and t.get("name")]
        created_by = (payload.get("details") or {}).get("created_by") or {}
        # 作者只认**这一层**的字段; created_by 是主题创建者 (楼主), 指定楼层时用它
        # 会把楼层的作者显示成楼主 (用户报过同类问题: 时间/点赞也踩过)。只有解析的
        # 就是主楼时, 才允许回落到 created_by (楼层的 name 字段可能缺失)。
        is_opening = first.get("post_number") == 1
        author_handle = str(first.get("username") or (created_by.get("username") if is_opening else "") or "")
        author_name = str(
            first.get("name")
            or first.get("username")          # 楼层可能没有 name, 用 username 而不是楼主名
            or (created_by.get("name") if is_opening else "")
            or ""
        )

        return cls(
            topic_id=str(payload.get("id") or topic_id),
            title=str(payload.get("title") or payload.get("fancy_title") or ""),
            markdown_content=markdown_content,
            text_content=text_content,
            author_name=author_name,
            author_handle=author_handle,
            # 时间与点赞取**该楼层**的: 主题级 created_at / like_count 是楼主帖与全话题的,
            # 指定楼层时用它们会显示成别人的时间与赞数
            published_at=str(first.get("created_at") or payload.get("created_at") or ""),
            view_count=to_int(payload.get("views")),
            like_count=to_int(
                first.get("reaction_users_count")
                if first.get("reaction_users_count") is not None
                else payload.get("like_count")
            ),
            reply_count=to_int(payload.get("reply_count")),
            tags=tags,
            images=images,
            quoted_media_count=quoted_media_count,
            # 只认平台自己的标记: 标签里的 NSFW
            is_sensitive=any(t.casefold() == "nsfw" for t in tags),
            # 当前这一层的楼层号 —— 分享楼层时用它标出"这是第几楼"
            post_number=to_int(first.get("post_number")),
        )

    #: 引用块在正文里的占位标记 (转 markdown 后再替换成渲染结果)
    _QUOTE_PLACEHOLDER = "@@linuxdo-quote-{}@@"

    @classmethod
    def _post_to_quote(cls, post: dict[str, Any]) -> tuple[str, list[LinuxDoImage]]:
        """把**另一层**的内容渲染成引用块 + 它自己的图片。

        返回 ``(引用块 markdown, 该层的图片)``:

        - 引用块**不内联图片** —— 只给作者行与文字, 图片由调用方按"引用块媒体"
          的通道单独带过去 (与 twitter 的被引用帖媒体同一机制)。
        - **纯图楼层也要返回引用块**(只有作者行): 这层确实在回应它, 直接把整层丢掉
          是**有损**的 —— 用户看不到"在回复谁"。
        """
        cooked = post.get("cooked") or ""
        username = str(post.get("username") or "")
        author = format_author_link(
            str(post.get("name") or ""), username, profile_url(Platform.LINUXDO, username)
        )
        floor = post.get("post_number")
        head = f"{author} · #{floor}" if floor else author
        if not cooked:
            return "", []

        soup = BeautifulSoup(cooked, "lxml")
        images = cls._extract_images(soup)  # 图片先取出来 (下面会清理掉)
        # 先摘掉引用块与媒体外壳, 只要这一层自己的文字
        for aside in soup.find_all("aside", class_="quote"):
            aside.decompose()
        cls._simplify(soup)
        text = soup.get_text("\n", strip=True)
        # 没文字也出引用块 (sign_only) —— 纯图楼层只有署名, 图片按引用块媒体接进块内
        return format_quote_block(text, head, sign_only=True), images

    @staticmethod
    def _quote_to_plain(block: str) -> str:
        """引用块 markdown → 纯文本 (给 text_content 兜底路径用)。"""
        text = re.sub(r"<[^>]+>", "", block or "")
        lines = [re.sub(r"^>\s*", "", ln).strip() for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    @classmethod
    def _context_quotes(
        cls, posts: list[dict[str, Any]], current: dict[str, Any]
    ) -> tuple[list[str], list[LinuxDoImage]]:
        """当前楼层的上下文: 引用块 (**从远到近**) + 这些层的图片。

        用户要求: 分享某一层时要把**主楼**也带上 (那一层常是在回应主楼/前文);
        楼层回复了别的楼层时, 被回复的那层也要带上。

        被带上的那些层自己的图片也一并返回 —— 由调用方按"引用块媒体"通道发送
        (纯图楼层只有图片, 不带就等于把主楼丢了)。
        """
        wanted = current.get("post_number")
        blocks: list[str] = []
        images: list[LinuxDoImage] = []
        targets: list[dict[str, Any]] = []
        # ① 主楼: 当前就是主楼时不用带
        if wanted != 1:
            if op := next((p for p in posts if p.get("post_number") == 1), None):
                targets.append(op)
        # ② 被回复的楼层 (回复主楼时已在①里, 不重复)
        reply_to = current.get("reply_to_post_number")
        if reply_to and reply_to not in (1, wanted):
            if rp := next((p for p in posts if p.get("post_number") == reply_to), None):
                targets.append(rp)

        for post in targets:
            block, post_images = cls._post_to_quote(post)
            if block:
                blocks.append(block)
            images.extend(post_images)
        return blocks, images

    @classmethod
    def _extract_quotes(cls, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """把 Discourse 的引用回复 (``aside.quote``) 抽出来, 交给**公共**排版 helper 渲染。

        排版规则由 ``format_quote_block`` 决定 (整块斜体、作者行在块内、不写"引用/回复"字样),
        与 twitter / threads 完全一致 —— 这里不要再自己拼引用块的 markdown。
        """
        rendered: list[tuple[str, str]] = []
        for index, aside in enumerate(soup.find_all("aside", class_="quote")):
            username = str(aside.get("data-username") or "")
            blockquote = aside.find("blockquote")
            text = blockquote.get_text("\n", strip=True) if blockquote else ""
            placeholder = cls._QUOTE_PLACEHOLDER.format(index)
            aside.replace_with(placeholder)
            if not text:
                rendered.append((placeholder, ""))
                continue
            author = format_author_link("", username, profile_url(Platform.LINUXDO, username))
            rendered.append((placeholder, format_quote_block(text, author)))
        return rendered

    @staticmethod
    def _strip_lightbox(soup: BeautifulSoup) -> None:
        """去掉图片的 lightbox 包裹 —— 图会作为媒体单独发送, 正文里不再重复。

        清掉的是**整个包裹**: 除了 ``img``, 里面还有 ``div.meta`` 里的说明文字
        (``span.filename`` 是文件名, ``span.informations`` 是"分辨率 体积")。
        那些文字不是正文, 见 ``_from_payload`` 里为什么要在取纯文本前调用。
        """
        for wrapper in soup.find_all("div", class_="lightbox-wrapper"):
            wrapper.decompose()

    @staticmethod
    def _simplify(soup: BeautifulSoup, polls: list[dict[str, Any]] | None = None) -> None:
        """清理 Discourse 特有的包裹结构，避免转换出噪音。

        - ``div.lightbox-wrapper``：图片会作为媒体单独发送，正文里不再重复
        - ``details`` / ``summary``：富文本渲染不支持折叠，这里展开（保留内容、去掉外壳标题）
        - ``div.spoiler``：只保留内容
        - ``img.emoji`` / ``img.avatar``：直接去掉 —— emoji 的 alt 是 ``:name:`` 形式,
          留在正文里既不美观也不是原样文本; 头像则是引用回复头部的装饰
        - ``div.poll``：改写成结构化的投票块（见 ``_convert_polls``）
        """
        for tag in soup.find_all("img", class_=["emoji", "avatar"]):
            tag.decompose()
        LinuxDoTopic._strip_lightbox(soup)
        for details in soup.find_all("details"):
            details.unwrap()
        for summary in soup.find_all("summary"):
            summary.decompose()
        for spoiler in soup.find_all("div", class_="spoiler"):
            spoiler.unwrap()
        LinuxDoTopic._convert_polls(soup, polls or [])

    @staticmethod
    def _convert_polls(soup: BeautifulSoup, polls: list[dict[str, Any]]) -> None:
        """把 Discourse 的投票改写成「标题（人数）+ 选项列表（票数 / 占比）」。

        不处理的话，poll 就是一段 HTML，直接转 markdown 会把**选项与人数标签**
        糊成一串裸文字（如 ``选项A / 选项B / 0 / 投票人``）—— 看不出这是投票，
        最后那个 ``0 投票人`` 还像是正文的残句。

        **真实数据在同一次响应的 ``post["polls"]`` 结构化字段里**
        （``options[].votes`` / ``voters``）—— ``cooked`` 里那个 ``info-number``
        是恒为 0 的**占位壳**（服务端渲染与前端首次渲染都用它，真实数字由前端拿
        结构化数据填）。所以这里以 ``polls`` 为准，**不需要任何额外请求**。

        ⚠️ 教训: 判断"数据拿不到"之前要把**同一个响应里的其它字段**翻一遍 ——
        只看 ``cooked`` 会得出"票数要另请求"的错误结论（踩过）。
        没有结构化数据时（老版本站点）退化成「📊 投票」+ 选项，**不显示那个假 0**。
        """
        by_name = {str(p.get("name")): p for p in polls}
        for index, poll in enumerate(soup.find_all("div", class_="poll")):
            name = str(poll.get("data-poll-name") or "")
            data = by_name.get(name) or (polls[index] if index < len(polls) else {})
            raw_options = data.get("options") or []
            rows: list[tuple[str, int | None]] = []
            for option in raw_options:
                if isinstance(option, dict):
                    rows.append((LinuxDoTopic._option_text(option), to_int(option.get("votes"))))
            if not rows:
                # 没有结构化数据: 退回 cooked 里的选项文字 (不带票数, 见 docstring)
                rows = [
                    (li.get_text(" ", strip=True), None) for li in poll.select("li[data-poll-option-id]")
                ]
            if not rows:
                continue
            voters = to_int(data.get("voters")) or 0
            title = str(data.get("title") or "").strip()

            block = soup.new_tag("div")
            head = soup.new_tag("p")
            parts = ["📊 投票"]
            if title:
                parts.append(f"「{title}」")
            if voters > 0:
                parts.append(f"{voters} 人参与")
            head.string = parts[0] + (f"（{' · '.join(parts[1:])}）" if len(parts) > 1 else "")
            block.append(head)

            if all(votes is not None for _, votes in rows) and voters > 0:
                # 有真实票数 ⇒ 表格（markdownify 会转成 markdown 表格语法, 服务端
                # 解析成 RichBlockTable —— 已实测）
                block.append(LinuxDoTopic._poll_table(soup, rows, voters))
            else:
                # 没有结构化数据（老站点）: 只列选项, **不显示那个恒为 0 的占位人数**
                options_list = soup.new_tag("ul")
                for text, _votes in rows:
                    item = soup.new_tag("li")
                    item.string = text
                    options_list.append(item)
                block.append(options_list)
            poll.replace_with(block)

    @staticmethod
    def _poll_table(soup: BeautifulSoup, rows: list[tuple[str, int | None]], voters: int):
        """把投票选项构造成 HTML 表格（→ markdown 表格 → 服务端的 Table 块）。"""
        table = soup.new_tag("table")
        thead = soup.new_tag("thead")
        head_row = soup.new_tag("tr")
        for label in ("选项", "票数", "占比"):
            th = soup.new_tag("th")
            th.string = label
            head_row.append(th)
        thead.append(head_row)
        table.append(thead)
        tbody = soup.new_tag("tbody")
        for text, votes in rows:
            tr = soup.new_tag("tr")
            for value in (text, str(votes), f"{round((votes or 0) * 100 / voters)}%"):
                td = soup.new_tag("td")
                td.string = value
                tr.append(td)
            tbody.append(tr)
        table.append(tbody)
        return table

    @staticmethod
    def _option_text(option: dict[str, Any]) -> str:
        """选项文字: ``html`` 字段可能带标签 (有的投票选项就是一张图)。"""
        raw = str(option.get("html") or "")
        if "<" not in raw:
            return raw.strip()
        return BeautifulSoup(raw, "lxml").get_text(" ", strip=True)

    @staticmethod
    def _to_markdown(html: str) -> str:
        """HTML → markdown。"""
        if not html:
            return ""
        converter = MarkdownConverter(heading_style="ATX")
        markdown_content = str(converter.convert(html))
        # 去掉转换后的空行堆积（删掉表情/头像后可能多出空段落）
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
        return markdown_content.strip()

    @staticmethod
    def _extract_images(soup: BeautifulSoup) -> list[LinuxDoImage]:
        """取帖子里的图片: lightbox 的 href 是原图, img 的宽高用来声明显示比例。"""
        images: list[LinuxDoImage] = []
        seen: set[str] = set()

        for img in soup.find_all("img"):
            classes = img.get("class") or []
            # 表情 (img.emoji) 与头像 (img.avatar, 引用回复里那个 24x24 小图) 都不是帖子媒体
            if "emoji" in classes or "avatar" in classes:
                continue
            src = str(img.get("src") or "")
            link = img.find_parent("a")
            original = str((link.get("href") if link else "") or src)
            url = original or src
            if not url or url in seen:
                continue
            seen.add(url)
            images.append(
                LinuxDoImage(
                    url=url,
                    width=to_int(img.get("width")) or 0,
                    height=to_int(img.get("height")) or 0,
                )
            )
        return images


__all__ = ["LinuxDoError", "LinuxDoImage", "LinuxDoTopic"]
