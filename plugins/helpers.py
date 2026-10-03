"""plugins 共用的工具函数和数据类"""

import html
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from easy_ai18n import LocaleContent
from parsehub import ParseHub, Platform
from parsehub.types import AnyParseResult, RichTextParseResult
from pyrogram.types import Message

from i18n import t_
from log import logger
from repo.settings import SettingsConfig

logger = logger.bind(name="Helpers")

COMMANDS = {
    "start": t_("开始"),
    "jx": t_("解析并预览内容"),
    "raw": t_("发送原文件, 避免画质压缩"),
    "zip": t_("打包发送, 附带解析信息"),
    "jxjx": t_("绕过缓存解析"),
    "lang": t_("语言"),
    "cfg": t_("配置"),
}


def build_start_text() -> LocaleContent:
    return t_(
        f"**发送分享链接以进行解析**\n\n"
        f"**支持的平台:**\n"
        f"<blockquote expandable>{get_supported_platforms()}</blockquote>\n\n"
        f"**命令列表:**\n"
        f"<blockquote expandable>"
        f"/jx <链接> - 解析并预览内容\n"
        f"/raw <链接> - 发送原文件, 避免画质压缩\n"
        f"/zip <链接> - 打包发送, 附带解析信息\n"
        f"/jxjx <链接> - 绕过缓存解析\n"
        f"/lang - 语言\n"
        f"/cfg - 配置\n"
        f"/cfg <频道用户名/链接/id> - 频道配置\n"
        f"</blockquote>\n\n"
        f"**开源地址: [GitHub](https://github.com/z-mio/parse_hub_bot)**"
    )


def build_caption(
    parse_result: AnyParseResult,
    *,
    custom_content: str = "",
    config: SettingsConfig,
    rich: bool = False,
    allow_blockquote: bool = True,
    allow_expandable: bool = True,
    lang: str = "",
    view_label: str = "",
) -> str:
    return build_caption_by_str(
        parse_result.title,
        replace_url(parse_result.platform, parse_result.markdown_content)
        if rich and isinstance(parse_result, RichTextParseResult)
        else parse_result.content,
        parse_result.raw_url,
        platform=parse_result.platform,
        hide_source=config.hide_source,
        custom_content=custom_content,
        author_name=format_author_label(
            get_parse_author_name(parse_result), getattr(parse_result, "author_handle", "")
        ),
        hide_title=config.hide_title,
        hide_desc=config.hide_desc,
        rich=rich,
        allow_blockquote=allow_blockquote,
        allow_expandable=allow_expandable,
        metadata_line=build_metadata_line(
            published_at=getattr(parse_result, "published_at", None),
            view_count=getattr(parse_result, "view_count", None),
            like_count=getattr(parse_result, "like_count", None),
            lang=lang,
            view_label=view_label,
            like_label=t_("点赞") if view_label else "",
        ),
    )


#: 统计行里各段的分隔符
# 标签行的显示宽度预算 (全角记 2), 超出就截断: 一行大约三四十个全角字符
TAG_LINE_DISPLAY_BUDGET = 60

_METADATA_SEPARATOR = " · "

#: 元数据时间按该时区渲染 (容器是 UTC, 必须显式指定, 否则会差 8 小时)
_METADATA_TIMEZONE = "Asia/Shanghai"


def build_metadata_line(
    *,
    published_at: datetime | None = None,
    view_count: int | None = None,
    like_count: int | None = None,
    lang: str = "",
    view_label: str = "",
    like_label: str = "",
) -> str:
    """把发布时间/浏览量/点赞渲染成一行, 例如「19:00 · 2026年10月3日 · 1,455 查看 · 158 点赞」。

    平台不提供的项直接跳过, 不会留下空占位符。
    view_label / like_label 由调用方用 ``t_("查看")`` / ``t_("点赞")`` 提供以获得正确语言。
    """
    parts: list[str] = []
    if published_at:
        parts.extend(_format_published(published_at, lang))
    if view_count is not None:
        parts.append(f"{view_count:,} {view_label or t_('查看')}".strip())
    if like_count is not None:
        parts.append(f"{like_count:,} {like_label or t_('点赞')}".strip())
    return _METADATA_SEPARATOR.join(part for part in parts if part)


def _format_published(value: datetime, lang: str) -> list[str]:
    """返回 [时间, 日期] 两段 (一律 24 小时制, 中文用中文日期)。"""
    local = value
    try:
        local = value.astimezone(ZoneInfo(_METADATA_TIMEZONE))
    except Exception:  # noqa: BLE001 - 时区数据缺失时退回原时区, 不该让整条消息失败
        pass

    clock = f"{local.hour:02d}:{local.minute:02d}"
    if lang.startswith("zh"):
        return [clock, f"{local.year}年{local.month}月{local.day}日"]
    return [clock, local.strftime("%Y-%m-%d")]


def build_caption_by_str(
    title: str | None,
    content: str | None,
    raw_url: str,
    *,
    platform: Platform | None = None,
    hide_source: bool = False,
    custom_content: str = "",
    author_name: str = "",
    hide_title: bool = False,
    hide_desc: bool = False,
    rich: bool = False,
    allow_blockquote: bool = True,
    allow_expandable: bool = True,
    metadata_line: str = "",
) -> str:
    """构建消息正文：标题 + 内容 + 统计行 + 来源链接"""
    title, content = title or "", content or ""
    if rich:
        body = f"### {title}\n\n <details><summary>📃</summary>\n\n{content}\n\n</details>"
    else:
        parts = []
        if not hide_title and title:
            parts.append(f"**{neutralize_markdown(title)}**")
        if not hide_desc and content:
            # 正文里的 URL 也要中和: URL 中的 '__' 会被 markdown 解析插进 <i> 破坏链接
            parts.append(neutralize_markdown_urls(content))
        body = format_text(
            ("\n\n".join(parts)).strip(),
            allow_blockquote=allow_blockquote,
            allow_expandable=allow_expandable,
        )

    if author_name:
        label = neutralize_markdown(html.escape(author_name))
        body = f"<b>{label}:</b>\n\n{body}" if body else f"<b>{label}:</b>"

    if custom_content:
        body += f"\n\n{custom_content}"

    if metadata_line:
        body = f"{body}\n\n{metadata_line}" if body else metadata_line

    if hide_source:
        return body
    platform = platform or ParseHub().get_platform(raw_url)
    display = neutralize_markdown(html.escape(platform.display_name)) if platform else ""
    source = f"Source（{display}）" if platform else "Source"
    # href 里的 URL 必须中和: 否则 pyrogram 会把 URL 中的 '__' 解析成 <i> 塞进 href
    safe_url = neutralize_markdown(raw_url)
    return f"{body}\n\n{format_label(f"<a href='{safe_url}'>{source}</a>")}"


def build_rich_markdown(
    parse_result: AnyParseResult,
    *,
    config: SettingsConfig,
    lang: str = "",
    view_label: str = "",
    custom_content: str = "",
    media_placeholders: Sequence[str] = (),
) -> str:
    """构建富文本 (rich message) 正文: 标题 + 作者 + 原文格式正文 + 媒体 + 页尾。

    富文本由 Telegram 服务端解析 markdown, 所以正文直接沿用解析器给出的原文格式
    (标题/列表/引用/表格/加粗等都会被还原), 不像旧路径那样先转成 HTML 再拼 caption。
    统计数据与来源放在页尾的 <footer> 里。
    """
    title = (parse_result.title or "").strip()
    content = preserve_linebreaks(
        escape_setext_underlines(
            link_leading_hashtags(rich_content(parse_result).strip(), parse_result.platform)
        )
    )
    parts: list[str] = []
    if title and not config.hide_title:
        parts.append(f"### {title}")
    # 标签紧跟在标题正下方 (在作者行之前, 不在作者下方)
    if tag_line := format_tags(parse_result):
        parts.append(tag_line)
    if author := format_author_line(parse_result):
        parts.append(author)
    body_text, quote = split_trailing_quote(content) if content else ("", "")
    if body_text and not config.hide_desc:
        parts.append(body_text)
    if custom_content:
        parts.append(custom_content)

    parts.extend(wrap_collage(media_placeholders))

    if quote and not config.hide_desc:
        # 被引用/被回复的卡片: 主推自己的媒体要排在它上面 (与 X 上的观感一致)
        parts.append(quote)

    body = "\n\n".join(part for part in parts if part)

    footer_parts: list[str] = []
    metadata = build_metadata_line(
        published_at=getattr(parse_result, "published_at", None),
        view_count=getattr(parse_result, "view_count", None),
        like_count=getattr(parse_result, "like_count", None),
        lang=lang,
        view_label=view_label,
        like_label=t_("点赞") if view_label else "",
    )
    if metadata:
        footer_parts.append(metadata)
    if not config.hide_source:
        platform = parse_result.platform or ParseHub().get_platform(parse_result.raw_url)
        display = platform.display_name if platform else ""
        label = f"Source（{display}）" if display else "Source"
        # footer 里 markdown 链接语法不生效 (会原样显示成 "[文字](url)"), 必须用 HTML
        href = html.escape(parse_result.raw_url, quote=True)
        footer_parts.append(f'<a href="{href}">{html.escape(label)}</a>')

    if not footer_parts:
        return body
    footer = f"<footer>{_METADATA_SEPARATOR.join(footer_parts)}</footer>"
    return f"{body}\n\n---\n\n{footer}" if body else footer


def build_rich_markdown_by_str(
    title: str,
    content: str,
    raw_url: str,
    *,
    config: SettingsConfig,
    lang: str = "",
    view_label: str = "",
    author_name: str = "",
    author_handle: str = "",
    author_url: str = "",
    published_at: datetime | None = None,
    view_count: int | None = None,
    like_count: int | None = None,
    tags: Sequence[str] | None = None,
    custom_content: str = "",
    media_placeholders: Sequence[str] = (),
) -> str:
    """同 build_rich_markdown, 但直接吃字段 (缓存路径没有 ParseResult 对象)。"""
    return build_rich_markdown(
        _RichFields(  # type: ignore[arg-type]
            title,
            content,
            raw_url,
            author_name,
            author_handle,
            author_url,
            published_at,
            view_count,
            like_count,
            tags,
        ),
        config=config,
        lang=lang,
        view_label=view_label,
        custom_content=custom_content,
        media_placeholders=media_placeholders,
    )


class _RichFields:
    """最小 duck-type: 让 build_rich_markdown 能吃缓存里的字段。"""

    def __init__(
        self,
        title,
        content,
        raw_url,
        author_name,
        author_handle,
        author_url,
        published_at,
        view_count,
        like_count=None,
        tags=None,
    ):
        self.title = title or ""
        self.content = content or ""
        self.raw_url = raw_url
        self.author_name = author_name or ""
        self.author_handle = author_handle or ""
        self.author_url = author_url or ""
        self.published_at = published_at
        self.view_count = view_count
        self.like_count = like_count
        self.tags = list(tags or [])
        self.platform = None
        self.markdown_content = ""


def rich_content(parse_result: AnyParseResult) -> str:
    """富文本里要用的正文: 长文用 markdown 正文, 其它用纯文本正文。

    长文的 markdown_content 已经带原文结构, 直接用就能还原排版;
    其它类型 (视频/图文/多图) 的 content 同样是解析器产出的 markdown 片段。
    """
    markdown_content = getattr(parse_result, "markdown_content", "")
    if isinstance(parse_result, RichTextParseResult) and markdown_content:
        return markdown_content
    return parse_result.content or ""


# 行首的 "#标签" (# 后紧跟非空白, 标签里不含空白与常见标点)
_LINE_HASH_TAG_RE = re.compile(r"(?m)^([ \t]*)#([^\s#，。！？、,.!?）)】\]]+)")


def link_leading_hashtags(text: str, platform: Platform | None = None) -> str:
    """处理行首的 ``#标签``: 渲染成标签页链接 (拿不到标签页时只转义)。

    两件事同时解决:
    1. 行首 ``#xxx`` 是富文本 markdown 的一级标题语法, threads 正文末尾的
       ``#敬請準時收看`` 会变成巨大的 section heading;
    2. 裸 hashtag 里 Telegram 遇到 ``・`` 之类的字符就停止解析, 长标签会"断"。
    带空格的 ``# 标题`` 是真标题, 保持不动。
    """

    def repl(match: re.Match[str]) -> str:
        indent, tag = match.group(1), match.group(2)
        label = f"\\#{tag}"
        url = tag_page_url(platform, tag)
        if not url:
            return f"{indent}{label}"
        return f'{indent}<a href="{html.escape(url, quote=True)}">{label}</a>'

    return _LINE_HASH_TAG_RE.sub(repl, text)


# 整行只有 - 或 = 的行: markdown 会把它当成 setext 标题下划线, 把上面整段变成标题(大字)
_SETEXT_UNDERLINE_RE = re.compile(r"^([ \t]*)([-=]+)([ \t]*)$")


def escape_setext_underlines(text: str) -> str:
    """转义「整行只有 ``-`` 或 ``=``」的行, 防止它把上一行变成大标题。

    markdown 的 setext 语法里「文本行 + 下一行是若干 ``=`` 或 ``-``」= 一级/二级标题,
    而 ``--`` 两个减号就够触发。实测 threads 的节目表末尾有一行 ``--``, 结果**整个正文段**
    被服务端解析成 ``section heading``(大字)。转义首个字符即可, 渲染出来仍是原来的 ``--``。
    """
    lines = text.split("\n")
    return "\n".join(_SETEXT_UNDERLINE_RE.sub(r"\1\\\2\3", line) for line in lines)


def preserve_linebreaks(text: str) -> str:
    """把单换行变成 markdown 硬换行 (行尾两个空格)。

    富文本 markdown 解析时**单个换行会被吞成空格** —— threads 这种一行一条的
    排版会挤成一整段。行尾补两个空格即保留换行, 又不像空行那样拉开段间距。
    """
    lines = text.split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        followed_by_text = index + 1 < len(lines) and bool(lines[index + 1].strip())
        out.append(line.rstrip() + "  " if line.strip() and followed_by_text else line)
    return "\n".join(out)


def split_trailing_quote(content: str) -> tuple[str, str]:
    """把正文末尾的引用块拆出来, 返回 (引用之前的部分, 引用块)。

    被引用的推文由解析器渲染成 markdown 引用块放在正文最后, 但主推自己的媒体
    按 X 的观感应该紧跟主推文字、在被引用卡片**之上**, 所以先把尾部引用块摘出来,
    由调用方把媒体插在它前面。只有连续的行首 ``>`` 才算引用块, 不会跨普通空行,
    因此不会误吃正文前面的 "回复" 引用块。
    """
    lines = content.split("\n")
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    start = end
    while start > 0 and lines[start - 1].lstrip().startswith(">"):
        start -= 1
    if start >= end:
        return content, ""
    return "\n".join(lines[:start]).rstrip(), "\n".join(lines[start:end]).strip()


def wrap_collage(placeholders: Sequence[str]) -> list[str]:
    """多张媒体包成一个图集块。

    富文本里多个独立的图片块会渲染成各自分散的图; 包进 ``<tg-collage>`` 才是图集。
    单张直接返回原样。
    """
    if len(placeholders) <= 1:
        return list(placeholders)
    return ["<tg-collage>\n\n" + "\n".join(placeholders) + "\n\n</tg-collage>"]


# 各平台的标签页/标签搜索页. 没有列出的平台退回纯文本 #标签
TAG_PAGE_URLS: dict[Platform, str] = {
    Platform.PIXIV: "https://www.pixiv.net/tags/{tag}",
    Platform.TWITTER: "https://x.com/hashtag/{tag}",
    Platform.WEIBO: "https://s.weibo.com/weibo?q=%23{tag}%23",
    Platform.BILIBILI: "https://search.bilibili.com/all?keyword={tag}",
    Platform.THREADS: "https://www.threads.com/search?q=%23{tag}",
    Platform.TIKTOK: "https://www.tiktok.com/tag/{tag}",
    Platform.INSTAGRAM: "https://www.instagram.com/explore/tags/{tag}/",
    Platform.YOUTUBE: "https://www.youtube.com/hashtag/{tag}",
    Platform.FACEBOOK: "https://www.facebook.com/hashtag/{tag}",
    Platform.DOUYIN: "https://www.douyin.com/search/{tag}",
    Platform.KUAISHOU: "https://www.kuaishou.com/search/video?searchKey={tag}",
    Platform.ZHIHU: "https://www.zhihu.com/search?q={tag}",
    Platform.TIEBA: "https://tieba.baidu.com/f/search/res?qw={tag}",
    Platform.DOUBAN: "https://www.douban.com/search?q={tag}",
    Platform.XHS: "https://www.xiaohongshu.com/search_result?keyword={tag}",
    # Discourse 论坛: /tag/<名字> 会重定向到规范地址 /tag/<slug>/<id>, 浏览器自动跟随
    Platform.LINUXDO: "https://linux.do/tag/{tag}",
}


def tag_page_url(platform: Platform | None, tag: str) -> str:
    """平台上的标签页地址; 没有对应模板时返回空串"""
    template = TAG_PAGE_URLS.get(platform) if platform else None
    return template.format(tag=quote(str(tag), safe="")) if template else ""


def _render_tag(platform: Platform | None, tag: str) -> str:
    """单个标签: 能拿到标签页就渲染成链接, 否则退回纯文本"""
    label = f"\\#{html.escape(str(tag))}"
    url = tag_page_url(platform, tag)
    if not url:
        return label
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def format_tags(parse_result: AnyParseResult) -> str:
    """把作品标签渲染成一行, 尽量渲染成指向标签页的链接。

    两个坑都靠链接绕开:
    1. 行首的 ``#标签`` 在富文本 markdown 里是一级标题语法, 字号会大得离谱;
    2. Telegram 解析 hashtag 时遇到 ``・`` 之类的非字母字符就停, 长标签
       (如 ``アリサ・ミハイロヴナ・九条``) 只会染蓝到 ``・`` 之前, 看着像被截断。
    链接形式两者都没有, 还多了点击进标签页的能力。

    标签多时一行会很长 (pixiv 一条作品常有 8+ 个): 按显示宽度预算截断,
    超出部分省略成 ``…``, 保证整行落在一行到两行之间。
    """
    tags = getattr(parse_result, "tags", None) or []
    if not tags:
        return ""
    platform = getattr(parse_result, "platform", None) or ParseHub().get_platform(
        getattr(parse_result, "raw_url", "") or ""
    )

    rendered: list[str] = []
    width = 0
    for tag in tags:
        # +1 是标签之间的空格
        piece = _display_width(f"#{tag}") + 1
        if rendered and width + piece > TAG_LINE_DISPLAY_BUDGET:
            rendered.append("…")
            break
        rendered.append(_render_tag(platform, tag))
        width += piece
    return " ".join(rendered)


def _display_width(text: str) -> int:
    """按观感算显示宽度: 全角 (CJK 等) 记 2, 其余记 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def get_parse_author_name(parse_result: AnyParseResult) -> str:
    """读取解析器提供的文章作者，兼容未来平台扩展。"""
    for field in ("author_name", "author", "author_info", "user"):
        value = getattr(parse_result, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            continue
        for name_field in ("name", "full_name", "display_name", "username"):
            name = getattr(value, name_field, None)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def format_author_label(name: str, handle: str = "") -> str:
    """把作者名与 handle 拼成一行标签。

    - 两者都有且不同 → ``名字 @handle``
    - 两者相同 (忽略大小写、首尾空白与 handle 的 ``@`` 前缀) → 只留 ``@handle``
    - 只有 name → 返回 name; 只有 handle → 返回 ``@handle``; 都没有 → 空串

    handle 传入时可能已带 ``@`` (如 ``"@abc"``), 会统一去掉前缀, 避免出现 ``@@``。
    """
    name = (name or "").strip()
    handle = (handle or "").strip().lstrip("@").strip()
    if not name:
        return f"@{handle}" if handle else ""
    if not handle:
        return name
    if name.casefold() == handle.casefold():
        return f"@{handle}"
    return f"{name} @{handle}"


def format_author_line(parse_result: AnyParseResult) -> str:
    """作者行 (markdown): ``**名字 @handle：**``。

    有作者主页地址时, ``@handle`` 渲染成指向主页的链接 (富文本里 markdown 链接
    语法不生效, 必须用 HTML ``<a href>``); 没有地址就保持纯文本。
    """
    label = format_author_label(
        get_parse_author_name(parse_result), getattr(parse_result, "author_handle", "")
    )
    if not label:
        return ""
    handle = str(getattr(parse_result, "author_handle", "") or "").strip().lstrip("@").strip()
    url = str(getattr(parse_result, "author_url", "") or "").strip()
    if url and handle and f"@{handle}" in label:
        href = html.escape(url, quote=True)
        link = f'<a href="{href}">@{html.escape(handle)}</a>'
        label = label.replace(f"@{handle}", link)
    return f"**{label}：**"


_QUOTE_BLOCK_RE = re.compile(r"(?m)^>[^\n]*(?:\n>[^\n]*)*")

# 折叠阈值按实际观感定: 中文一行约 30 字符, 350 字符已是十来行, 再长就该折起来。
# 正文与引用块共用同一套阈值, 保证折叠规则统一。
_FOLD_CHAR_THRESHOLD = 350
_FOLD_LINE_THRESHOLD = 8


def _should_fold(text: str) -> bool:
    """文本是否超过折叠阈值 (字符数或行数任一超出)."""
    return len(text) > _FOLD_CHAR_THRESHOLD or len(text.splitlines()) > _FOLD_LINE_THRESHOLD

# pyrogram 的 Markdown 解析器 (client 默认 ParseMode.DEFAULT) 把成对的
# __ ** -- ~~ || ` 当格式定界符, 定界符字符本身会被吃掉 —— 引用块里的推文 handle
# (@__yuuuumr__ 显示成 @yuuuumr 并变成斜体) 就是这么被误伤的。
# 换成 HTML 数字实体: markdown 阶段不再匹配, 后续 html.parse 阶段还原成原字符,
# 最终显示完全一致。
_MD_DELIM_ENTITY = {
    ord("_"): "&#95;",
    ord("*"): "&#42;",
    ord("~"): "&#126;",
    ord("`"): "&#96;",
    ord("|"): "&#124;",
}


def neutralize_markdown(text: str) -> str:
    """中和 Markdown 格式定界符, 防止正文本被 pyrogram 的 markdown 解析误伤。"""
    return text.translate(_MD_DELIM_ENTITY)


_URL_RE = re.compile(r"https?://[^\s<>]+")


def neutralize_markdown_urls(text: str) -> str:
    """只中和 URL 里的 markdown 定界符。

    URL 里出现 '__' 时 (例如 https://x.com/__yuuuumr__/status/...), pyrogram 的
    markdown 解析器会把它当斜体定界符并插入 <i>/</i>, 于是
    <a href='https://x.com/__yuuuumr__/...'> 的 href 被写成
    'https://x.com/<i>yuuuumr</i>/...' —— 链接直接作废、变成不可点的纯文本。
    只处理 URL 片段, 避免影响正文里有意写的 markdown。
    """
    return _URL_RE.sub(lambda m: neutralize_markdown(m.group(0)), text)


def _strip_quote_markers(block: str) -> str:
    """去掉引用块每行行首的 '>', 并中和块内的 markdown 定界符。"""
    lines = block.rstrip("\n").split("\n")
    body = "\n".join(line[1:].lstrip() if line.startswith(">") else line for line in lines)
    return neutralize_markdown(body)


def _strip_quote_prefix(match: re.Match) -> str:
    return _strip_quote_markers(match.group(0))


def _split_quote_segments(text: str) -> list[tuple[bool, str]]:
    """把文本切成 (是否引用块, 片段) 序列; 引用块与其间文本各自独立成段。"""
    segments: list[tuple[bool, str]] = []
    pos = 0
    for match in _QUOTE_BLOCK_RE.finditer(text):
        if match.start() > pos:
            segments.append((False, text[pos : match.start()]))
        segments.append((True, match.group(0)))
        pos = match.end()
    if pos < len(text):
        segments.append((False, text[pos:]))
    return segments


def _render_foldable(content: str) -> str:
    """包成可折叠 blockquote (长内容展示用)。"""
    return f"<blockquote expandable>{content}</blockquote>"


def convert_markdown_quote(
    text: str, *, allow_blockquote: bool = True, allow_expandable: bool = True
) -> str:
    """把 Markdown 引用行 (以 '>' 开头) 转成 Telegram 的 <blockquote>。

    引用块超长时按与正文相同的阈值折叠成 <blockquote expandable> (统一折叠规则);
    allow_expandable=False 时引用块保持展开。

    allow_blockquote=False 时把引用前缀整个去掉: 内联消息带 blockquote 实体时,
    Telegram 会丢掉整批格式、整条消息变成纯文本。注意不能只跳过 <blockquote> 标签,
    pyrogram 的 Markdown 解析器只要看到行首 '>' 就会重新生成 blockquote 实体,
    所以那条通道必须把 '>' 前缀也剥掉。
    """
    if not allow_blockquote:
        return _QUOTE_BLOCK_RE.sub(_strip_quote_prefix, text)

    def _replace(match: re.Match) -> str:
        body = _strip_quote_markers(match.group(0))
        if allow_expandable and _should_fold(body):
            return _render_foldable(body)
        return f"<blockquote>{body}</blockquote>"

    return _QUOTE_BLOCK_RE.sub(_replace, text)


def format_text(text: str, *, allow_blockquote: bool = True, allow_expandable: bool = True) -> str:
    """格式化输出内容, 限制长度, 添加折叠块样式。

    折叠规则统一: 引用块与正文各段共用同一阈值 (字符数或行数任一超出即折叠),
    且各段独立折叠、互不外包 (Telegram 不支持嵌套 blockquote)。
    """
    text = text.strip()
    if len(text) > 1000:
        # 在 Markdown 阶段截断, 避免切断后面生成的 blockquote 标签
        text = text[:900] + "......"

    if not allow_blockquote:
        # 该通道不支持引用块: 剥掉前缀后按普通文本处理
        text = _QUOTE_BLOCK_RE.sub(_strip_quote_prefix, text)
        if allow_expandable and _should_fold(text):
            return _render_foldable(text)
        return text

    out: list[str] = []
    for is_quote, segment in _split_quote_segments(text):
        if is_quote:
            out.append(convert_markdown_quote(segment, allow_expandable=allow_expandable))
            continue
        core = segment.strip()
        if allow_expandable and core and _should_fold(core):
            # 保留片段两侧空白 (块间分隔), 只折叠核心内容
            lead = segment[: len(segment) - len(segment.lstrip())]
            trail = segment[len(segment.rstrip()) :]
            out.append(f"{lead}{_render_foldable(core)}{trail}")
        else:
            out.append(segment)
    return "".join(out)


def replace_url(platform: Platform | None, v: str) -> str:
    match platform:
        case Platform.WEIXIN:
            v = v.replace("mmbiz.qpic.cn", "qpic.cn.in/mmbiz.qpic.cn")
        case Platform.COOLAPK:
            v = v.replace("image.coolapk.com", "qpic.cn.in/image.coolapk.com")
        case Platform.DOUBAN:
            # 豆瓣图片分片域名 img1~imgN.doubanio.com
            v = re.sub(r"img\d+\.doubanio\.com", r"qpic.cn.in/\g<0>", v)
    return v


def get_supported_platforms() -> str:
    text: list[str] = []
    for i in ParseHub().get_platforms():
        text.append(f"**{i['name']}** __({'__, __'.join(i['supported_types'])})__")
    text.sort(reverse=True)
    return "\n".join(text)


def format_label(text: str) -> str:
    return f"<b>▎{text}</b>"


def parse_channel_ref(value: str) -> int | str:
    """解析频道 ID、用户名或 Telegram 链接，忽略 URL 参数、片段和消息 ID。"""
    channel_id_re = re.compile(r"-100\d{1,19}\Z")
    internal_channel_id_re = re.compile(r"[1-9]\d{0,19}\Z")
    username_re = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}\Z")
    telegram_link_hosts = frozenset({"t.me", "telegram.me"})

    value = value.strip()
    if not value:
        raise ValueError("频道引用不能为空")

    if channel_id_re.fullmatch(value):
        return int(value)

    url = urlsplit(value if "://" in value else f"https://{value}")
    if url.netloc.lower() in telegram_link_hosts:
        if url.scheme not in {"http", "https"}:
            raise ValueError("仅支持 HTTP 或 HTTPS Telegram 链接")

        path_parts = tuple(part for part in url.path.split("/") if part)
        match path_parts:
            case ("c", internal_channel_id) | ("c", internal_channel_id, _):
                if not internal_channel_id_re.fullmatch(internal_channel_id):
                    raise ValueError("无效的 /c/ 频道链接")
                return int(f"-100{internal_channel_id}")

            case (username,):
                if username_re.fullmatch(username):
                    return f"@{username}"
                raise ValueError("频道用户名格式无效")

            case _:
                raise ValueError("Telegram 链接必须包含用户名或 /c/ 频道 ID")

    if "://" in value:
        raise ValueError("仅支持 t.me 或 telegram.me 链接")

    username = value.removeprefix("@")
    if not username_re.fullmatch(username):
        raise ValueError("频道用户名格式无效")

    return f"@{username}"


def get_thread_id(msg: Message) -> int | None:
    return msg.message_thread_id or (1 if msg.chat and msg.chat.is_forum else None)
