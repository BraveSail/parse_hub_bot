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
from parsehub.utils.helpers import SPOILER_FOLD_SUMMARY, format_author_label, format_author_link
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
        f"**开源地址: [GitHub](https://github.com/BraveSail/shirobako)**"
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
    max_length: int | None = None,
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
            like_label=t_[lang]("点赞") if (view_label and lang) else "",
        ),
        max_length=max_length,
        fold_summary=t_[lang]("展开全文") if lang else "",
        lang=lang,
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
    """把发布时间/浏览量/点赞渲染成一行, 例如「2026年10月3日 19:00 · 1,455 查看 · 158 点赞」。

    时间那段是个时间戳实体 (客户端会按本地时区重新渲染并本地化)。

    平台不提供的项直接跳过, 不会留下空占位符。
    view_label / like_label 由调用方用 ``t_[lang]("查看")`` / ``t_[lang]("点赞")`` 提供;
    不传时按 ``lang`` 兜底 (以前兜底走模块级 ``t_`` = 默认语言, 与用户语言不一致)。
    """
    parts: list[str] = []
    if published_at:
        parts.extend(_format_published(published_at, lang))
    if view_count is not None:
        fallback = t_[lang]("查看") if lang else t_("查看")
        parts.append(f"{view_count:,} {view_label or fallback}".strip())
    if like_count is not None:
        like_fallback = t_[lang]("点赞") if lang else t_("点赞")
        parts.append(f"{like_count:,} {like_label or like_fallback}".strip())
    return _METADATA_SEPARATOR.join(part for part in parts if part)


def _format_published(value: datetime, lang: str) -> list[str]:
    """发布时间 —— 走 Telegram 的时间戳实体, 由**客户端按本地时区**渲染。

    服务端只发一个 UTC unix 时间戳 (``<tg-time unix=...>``), 显示交给客户端:
    于是每个用户看到的时间都与自己的时区相符, 而不是消息里写死的那一刻。
    (以前固定按 ``Asia/Shanghai`` 算好再发出去 —— 人在别的时区看到的就是"北京时间"。)

    ``format="Dt"`` = 长日期 + 短时间, 即「2026年10月4日 12:34」; 短时间 ``t`` 是
    24 小时制 (``16:20``)。实际呈现仍由客户端本地化, 12/24 小时制跟随用户设备设置。

    标签里的文字是**兜底**: 老客户端不认 ``tg-time`` 时照常显示, 所以内容与
    客户端渲染的形态保持一致。
    """
    local = value
    try:
        local = value.astimezone(ZoneInfo(_METADATA_TIMEZONE))
    except Exception:  # noqa: BLE001 - 时区数据缺失时退回原时区, 不该让整条消息失败
        pass

    clock = f"{local.hour:02d}:{local.minute:02d}"
    # 兜底文字: 中日用「年月日」书写 (日语也一样); 其余语言用 ISO 日期
    if lang.startswith(("zh", "ja")):
        fallback = f"{local.year}年{local.month}月{local.day}日 {clock}"
    else:
        fallback = f"{local.strftime('%Y-%m-%d')} {clock}"
    # unix 取自归一到 _METADATA_TIMEZONE 后的 local (aware), 与原来的日期算法同源
    return [f'<tg-time unix="{int(local.timestamp())}" format="Dt">{fallback}</tg-time>']


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
    max_length: int | None = None,
    fold_summary: str = "",
    lang: str = "",
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
            max_length=max_length,
            fold_summary=fold_summary,
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
    # 来源标签要跟随用户语言 (以前硬编码英文 "Source（…）", 任何语言都显示英文)
    _t = t_[lang] if lang else t_
    source = _t(f"来源（{display}）") if display else _t("来源")
    # href 里的 URL 必须中和: 否则 pyrogram 会把 URL 中的 '__' 解析成 <i> 塞进 href
    safe_url = neutralize_markdown(raw_url)
    return f"{body}\n\n{format_label(f"<a href='{safe_url}'>{source}</a>")}"


def _meta_divider(meta_parts: Sequence[str], parts: Sequence[str]) -> list[str]:
    """元信息 (标题/作者) 与内容之间的分割线。

    用户要求「作者下面的分割线」—— 让"谁发的"和"发了什么"之间有一道明显的界,
    与**内容与页脚之间**那条 (``body`` 拼接时加的 ``---``) 同一形态。

    三个条件都满足才插, 否则不加多余的线:

    - **有作者行** (只有标题时用户没要求, 不引入新的视觉变化)
    - **后面还有内容** (纯元信息没有要分隔的东西)
    - 元信息段里作者不是唯一一个空壳 (由调用方过滤后传入)
    """
    has_author = any(part and not part.startswith("### ") for part in meta_parts)
    has_content = any(part for part in parts)
    return ["---"] if (has_author and has_content) else []


def build_rich_markdown(
    parse_result: AnyParseResult,
    *,
    config: SettingsConfig,
    lang: str = "",
    view_label: str = "",
    custom_content: str = "",
    media_placeholders: Sequence[str] = (),
    quote_media_placeholders: Sequence[str] = (),
    reply_media_placeholders: Sequence[str] = (),
    hide_content: str = "",
    progress: str = "",
) -> str:
    """构建富文本 (rich message) 正文: 标题 + 作者 + 原文格式正文 + 媒体 + 页尾。

    :param hide_content: 用户手动要求遮住内容时**命中的标记** (``#nsfw`` /
        ``#spoiler``, 空串=没要求)。非空即把内容折成
        ``<details><summary>⚠️ <标记></summary>`` —— **不留预览**, 与自动折叠不同:
        那是为了别让长正文撑屏, 这是用户明确要藏起来。
        **除标题/作者外全部进折叠**(含图, 用户明确要求"所有东西都遮")。

    :param progress: 处理过程的阶段文案 (如 ``▎下 载 中...``), 放在**页尾的第一段**。
        处理过程用它渲染"最终排版的无媒体版": 主体 (标题/作者/正文/标签) 与结果
        完全一致, 只有页尾那段从"进度"变成"时间 · 统计"—— 位置不动, 不跳版。

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
    # 分两组: 标题/作者是"这是什么"的元信息, 留在折叠外; 其余(正文/引用块/标签/媒体)
    # 都是内容 —— 手动遮住时整组进 details (含图, 用户明确要求"所有东西都遮")。
    meta_parts: list[str] = []
    if title and not config.hide_title:
        # **一级标题** (`#`, size 1 —— 这个 API 里 1 最大、6 最小)。
        # 以前用的是 `###`(size 3), 结果帖子标题比正文里的一级小节还小
        # (实测某 linux.do 帖: 标题 size=3, 正文 `# 小节` size=1), 完全不像标题。
        meta_parts.append(f"# {title}")
    if author := format_author_line(parse_result):
        meta_parts.append(author)
    parts: list[str] = []
    # 被回复的卡片在正文前、被引用的卡片在正文后, 各自的媒体留在自己的块里
    reply_quote, body_text, quote = split_quote_blocks(content) if content else ("", "", "")
    fold_summary = t_[lang]("展开全文") if lang else ""
    # 手动遮住时的摘要: ⚠️ + **用户自己写的那个标记** (#nsfw / #spoiler)。
    # 不写「展开全文」—— 折叠按钮的文字对"被藏起来"这件事没有信息量, 而标记本身
    # 说明了为什么藏 (不宜公开 / 剧透); 也不必翻译, 标记是用户输入。
    spoiler_summary = f"⚠️ {hide_content}" if hide_content else SPOILER_FOLD_SUMMARY
    reply_media = list(reply_media_placeholders)
    # 末尾引用块不存在时, 属于它的媒体要交回**头部**引用块 (反之亦然, 下面那段)。
    # 平台产出"引用块在前"的结构时就会走到这里 (linux.do 把主楼做成引用块放最上面) ——
    # 不兜的话媒体的归属块为空, render_quote_card 直接丢弃它们 (症状: 图不见了)。
    if not config.hide_desc and quote_media_placeholders and not quote:
        if reply_quote:
            reply_media = [*reply_media, *quote_media_placeholders]
        else:
            media_placeholders = [*media_placeholders, *quote_media_placeholders]
        quote_media_placeholders = ()
    if reply_quote and not config.hide_desc:
        # 被回复的卡片也要能折叠: 以前直接拼进 parts, 1294 字的回复块整屏铺开
        parts.extend(render_quote_card(reply_quote, reply_media, summary=fold_summary))
        reply_media = []  # 已安置
    elif not reply_quote:
        # 没有独立的回复块 (例如正文为空): 媒体不能丢, 兜到末尾引用块或正文媒体里
        if quote:
            quote_media_placeholders = [*quote_media_placeholders, *reply_media]
        else:
            media_placeholders = [*media_placeholders, *reply_media]
        reply_media = []
    if body_text and not config.hide_desc:
        # 正文只折叠、不截断: 富文本没有媒体 caption 的 1024 限制,
        # 截断会把长正文变成省略号, 折叠也就轮不上了
        # (手动遮住时不在这里折 —— 整组内容会在下面一起进 details)
        parts.append(body_text if hide_content else format_text(body_text, fold_summary=fold_summary))
    if custom_content:
        parts.append(custom_content)

    # 标签是正文的收尾: 放在正文之后 (与正文之间自然空一行)
    if tag_line := format_tags(parse_result):
        parts.append(tag_line)

    parts.extend(wrap_collage(media_placeholders))

    if quote and not config.hide_desc:
        # 被引用/被回复的卡片: 主推自己的媒体要排在它上面 (与 X 上的观感一致)
        parts.extend(render_quote_card(quote, quote_media_placeholders, summary=fold_summary))

    if hide_content and (visible := [part for part in parts if part]):
        # 手动遮住: 内容整组进 details —— **正文、引用块、标签、媒体(含图集)全在里面**,
        # 且不留预览 (留了就等于没遮)。只保留标题/作者在外面, 否则看不出这是什么内容。
        inner = "\n\n".join(visible)
        folded = f"<details><summary>{spoiler_summary}</summary>\n\n{inner}\n\n</details>"
        body = "\n\n".join([*[p for p in meta_parts if p], *_meta_divider(meta_parts, parts), folded])
    else:
        body = "\n\n".join(part for part in [*meta_parts, *_meta_divider(meta_parts, parts), *parts] if part)

    footer_parts: list[str] = []
    # 处理过程的进度放页尾第一段: 与最终结果的"时间 · 统计"同一个位置, 主体不动
    if progress:
        footer_parts.append(progress)
    metadata = build_metadata_line(
        published_at=getattr(parse_result, "published_at", None),
        view_count=getattr(parse_result, "view_count", None),
        like_count=getattr(parse_result, "like_count", None),
        lang=lang,
        view_label=view_label,
        like_label=t_[lang]("点赞") if (view_label and lang) else "",
    )
    if metadata:
        footer_parts.append(metadata)
    if not config.hide_source:
        platform = parse_result.platform or ParseHub().get_platform(parse_result.raw_url)
        display = platform.display_name if platform else ""
        # 来源标签跟随用户语言 (以前硬编码英文 "Source", 任何语言都显示英文)
        label = t_[lang](f"来源（{display}）") if (display and lang) else (t_[lang]("来源") if lang else "Source")
        # footer 里 markdown 链接语法不生效 (会原样显示成 "[文字](url)"), 必须用 HTML
        href = html.escape(parse_result.raw_url, quote=True)
        footer_parts.append(f'<a href="{href}">{html.escape(label)}</a>')

    # 裸 URL 自己包成链接 (发送时关掉了服务端的实体自动识别, 见 linkify_bare_urls)
    if not footer_parts:
        return linkify_bare_urls(body)
    footer = f"<footer>{_METADATA_SEPARATOR.join(footer_parts)}</footer>"
    return linkify_bare_urls(f"{body}\n\n---\n\n{footer}" if body else footer)


def build_progress_markdown(
    parse_result: AnyParseResult | None = None,
    *,
    progress: str,
    config: SettingsConfig,
    lang: str = "",
    view_label: str = "",
    spoiler_tag: str = "",
    custom_content: str = "",
    raw_url: str = "",
) -> str:
    """处理过程的消息 —— 与最终结果**同一种排版**。

    用户要求: 处理过程别再用老格式的小字, 要和结果一致 (原话「处理过程的消息能不能和最终
    消息保持一致? 现在格式还是老格式, 只有最终消息是新的」)。

    两个分支:

    - **已有解析结果** → 直接走 ``build_rich_markdown`` (**不带媒体占位符**)。
      标题/作者/正文/标签/页脚全部就位, 与结果唯一的差别是没有媒体 ——
      视觉上就是"正文先出来, 图随后出现", 不再有格式跳变。
    - **还没有结果** (解析阶段) → 骨架: 进度行作正文 + 页尾的来源链接。
      结构与有结果时一致 (正文 + ``---`` + 页脚), 所以结果出来时不会跳版。

    进度文案统一放在**页尾第一段**: 与结果里的"时间 · 统计"同一个位置, 主体位置完全不动。

    :param spoiler_tag: 手动打码标记 —— **处理过程也必须遮**, 否则"下载中"那几秒
        就把该藏的内容露出来了。
    """
    if parse_result is not None:
        return build_rich_markdown(
            parse_result,
            config=config,
            lang=lang,
            view_label=view_label,
            custom_content=custom_content,
            hide_content=spoiler_tag,
            progress=progress,
        )

    # 还没有解析结果: 骨架。正文放进度, 页尾放来源 —— 与有结果时的结构一致
    href = html.escape(raw_url, quote=True) if raw_url else ""
    platform = ParseHub().get_platform(raw_url) if raw_url else None
    display = platform.display_name if platform else ""
    if href and display and lang:
        label = t_[lang](f"来源（{display}）")
    elif lang:
        label = t_[lang]("来源")
    else:
        label = "Source" if href else ""
    footer = f'<footer><a href="{href}">{html.escape(label)}</a></footer>' if href else ""
    return f"{progress}\n\n---\n\n{footer}" if footer else progress


#: 正文里**已经是链接**的片段 (显式锚点) —— 链接化时要跳过, 免得把 URL 包第二层。
_ANCHOR_SEGMENT_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.S)
#: 裸 URL。**不匹配** markdown 链接的目标 (`](url)`) —— 那是已经写好的链接。
_BARE_URL_RE = re.compile(r"""(?<!\]\()https?://[^\s<>"']+""")


def _linkify_segment(segment: str) -> str:
    """把一段文本里的裸 URL 包成显式锚点 (末尾标点留在锚点外)。"""

    def repl(match: re.Match) -> str:
        url, tail = match.group(0), ""
        while url and url[-1] in ".,;:!?）)，。！？、：；」』】":
            tail = url[-1] + tail
            url = url[:-1]
        if not url:
            return match.group(0)
        safe = html.escape(url, quote=True)
        return f'<a href="{safe}">{html.escape(url)}</a>{tail}'

    return _BARE_URL_RE.sub(repl, segment)


def linkify_bare_urls(text: str) -> str:
    """正文里的**裸 URL 变成可点链接**。

    为什么需要: 富文本发送开了 ``skip_entity_detection``（关掉服务端的实体自动识别，
    这是让作者行的 ``@handle`` **不可点击**的唯一手段）。代价就是裸 URL / ``#标签``
    不再被自动识别成链接 —— 那就**自己写成链接**（显式 ``<a href>`` 不受该开关影响,
    已实测）。已经写好的链接（``<a>`` / ``[文字](url)``）原样保留, 不重复包裹。
    """
    if "http" not in text:
        return text
    out: list[str] = []
    pos = 0
    for match in _ANCHOR_SEGMENT_RE.finditer(text):
        out.append(_linkify_segment(text[pos : match.start()]))
        out.append(match.group(0))  # 已是链接, 原样保留
        pos = match.end()
    out.append(_linkify_segment(text[pos:]))
    return "".join(out)


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
    quote_media_placeholders: Sequence[str] = (),
    reply_media_placeholders: Sequence[str] = (),
    hide_content: str = "",
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
        quote_media_placeholders=quote_media_placeholders,
        reply_media_placeholders=reply_media_placeholders,
        hide_content=hide_content,
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
        url = tag_page_url(platform, tag)
        if not url:
            return f"{indent}{_tag_label(tag, linked=False)}"
        label = _tag_label(tag, linked=True)
        return f'{indent}<a href="{html.escape(url, quote=True)}">{label}</a>'

    return _LINE_HASH_TAG_RE.sub(repl, text)


# 整行只有 - 或 = 的行: markdown 会把它当成 setext 标题下划线, 把上面整段变成标题(大字)
_SETEXT_UNDERLINE_RE = re.compile(r"^([ \t]*)([-=]+)([ \t]*)$")


def escape_setext_underlines(text: str) -> str:
    r"""转义「**紧跟在一行文字下面**的 ``-`` / ``=``」行, 防止它把上一行变成大标题。

    markdown 的 setext 语法里「文本行 + 下一行是若干 ``=`` 或 ``-``」= 一级/二级标题,
    而 ``--`` 两个减号就够触发。实测 threads 的节目表末尾有一行 ``--``, 结果**整个正文段**
    被服务端解析成 ``section heading``(大字)。转义首个字符即可, 渲染出来仍是原来的 ``--``。

    ⚠️ **判据必须带上"上一行有文字"** —— setext 不会跨空行。以前是无条件转义整行的分隔符,
    于是 discourse 那种**独立成行的 ``---`` (分隔线)** 也被转义成 ``\---``, 服务端不再认它,
    渲染成字面的 ``---`` 段落 (实测一篇 linux.do 长帖里 18 处)。独立行既不会触发 setext,
    也不该被转义 —— 它就该是分隔线。
    """
    lines = text.split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        # 只有上一行是**有内容的文字行**时才危险 (空行/开头都安全)
        after_text = index > 0 and bool(lines[index - 1].strip())
        if after_text:
            line = _SETEXT_UNDERLINE_RE.sub(r"\1\\\2\3", line)
        out.append(line)
    return "\n".join(out)


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


def split_quote_blocks(content: str) -> tuple[str, str, str]:
    """把正文拆成 (开头的引用块, 中间正文, 末尾的引用块)。

    解析器把**被回复**的推文渲染在正文最前、**被引用**的推文渲染在正文最后
    (与 X 上的卡片位置一致)。主推自己的媒体要夹在两个卡片之间, 所以两边都要摘出来。

    只认连续的行首 ``>``, 不跨普通空行。开头那个块必须紧贴正文开头 ——
    正文里自己写的引用 (中间位置) 不会被误当卡片。
    """
    lines = content.split("\n")

    # 末尾块
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    tail_start = end
    while tail_start > 0 and lines[tail_start - 1].lstrip().startswith(">"):
        tail_start -= 1

    # 开头块 (在末尾块之前)
    head_end = 0
    while head_end < tail_start and not lines[head_end].strip():
        head_end += 1
    head_scan = head_end
    while head_scan < tail_start and lines[head_scan].lstrip().startswith(">"):
        head_scan += 1
    # 只有确实是一整块引用 (后面跟空行或正文) 才算, 且不能把整个正文都吃进来
    head = "\n".join(lines[head_end:head_scan]).strip() if head_scan > head_end else ""

    middle = "\n".join(lines[head_scan:tail_start]).strip() if head else "\n".join(lines[:tail_start]).strip()
    tail = "\n".join(lines[tail_start:end]).strip()
    return head, middle, tail


def split_trailing_quote(content: str) -> tuple[str, str]:
    """兼容旧调用点: 返回 (去掉末尾引用块后的正文, 末尾引用块)。"""
    head, middle, tail = split_quote_blocks(content)
    body = f"{head}\n\n{middle}".strip() if head else middle
    return body, tail


def attach_quote_media(quote: str, placeholders: Sequence[str]) -> str:
    """把被引用内容的媒体接进引用块内部。

    富文本里引用块的成员是"连续以 ``>`` 开头的行", 所以占位符也必须带 ``> ``,
    否则媒体会掉到引用块外面 (实测: 不带前缀就变成独立的图片块)。
    多张时包成 ``<tg-collage>``, 与正文媒体一致 —— 引用块里嵌图集同样成立。
    """
    if not quote or not placeholders:
        return quote
    lines: list[str] = []
    for block in wrap_collage(placeholders):
        lines.extend(f"> {line}" if line.strip() else ">" for line in block.split("\n"))
    return "\n".join([quote, *lines])


def _quote_body(quote: str) -> str:
    """引用块去掉每行行首的 '> ' 后的正文。"""
    lines = quote.rstrip("\n").split("\n")
    return "\n".join(line[1:].lstrip() if line.startswith(">") else line for line in lines)


def quote_will_fold(quote: str) -> bool:
    """引用块会不会被折叠 (调用方据此决定媒体放块内还是块外)。"""
    return bool(quote) and _should_fold(_quote_body(quote))


def fold_quote_block(quote: str, *, summary: str = "") -> str:
    """把过长的引用块折起来。

    引用块的老折叠形态是**整体一个 `<blockquote expandable>`**（客户端自己
    显示开头几行），不像正文那样用 ``<details>`` 切成「预览 / 按钮 / 折起部分」
    三段 —— 用户明确反馈后者「引用块被按钮分割, 割裂感太强了」。
    """
    if not quote:
        return quote
    body = _quote_body(quote)
    if not _should_fold(body):
        return quote
    return render_expandable_quote(body)


def render_quote_card(quote: str, media: Sequence[str] = (), *, summary: str = "") -> list[str]:
    """渲染一张引用卡片（含它自己的媒体），返回若干段。

    **媒体的位置取决于折没折叠**（真机实测）：
    - 折叠块（``<blockquote expandable>``）内 ``![]()`` 图片语法**不解析**，
      会原样显示成 ``![]()`` 加一个链接（图片格式坏掉）；块内改用 ``<img>``
      虽然能出图，却会把块**退化成不可折叠**的普通引用块。
    - 普通 ``<blockquote>`` 内 ``![]()`` **正常出图**。

    ⇒ 会折叠时把媒体放到块**外**（文字折起来、图片在下面正常显示）；
    不折叠时留在块**内**（语义上属于引用内容）。
    """
    if not quote:
        return []
    if media and not quote_will_fold(quote):
        return [attach_quote_media(quote, media)]
    parts = [fold_quote_block(quote, summary=summary)]
    if media:
        parts.extend(wrap_collage(media))
    return parts


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


def _tag_label(tag: str, *, linked: bool) -> str:
    """标签的显示文本。

    ``#`` 需要转义是因为**行首**的 ``#xxx`` 会被当标题。带链接时行首是 ``<a``,
    本来就不是标题, 所以不加转义 —— 加了虽然渲染正常, 但复制消息时会露出反斜杠。
    退回纯文本时行首就是 ``#``, 必须转义。
    """
    escaped = html.escape(str(tag))
    return f"#{escaped}" if linked else f"\\#{escaped}"


def _render_tag(platform: Platform | None, tag: str) -> str:
    """单个标签: 能拿到标签页就渲染成链接, 否则退回纯文本"""
    url = tag_page_url(platform, tag)
    if not url:
        return _tag_label(tag, linked=False)
    return f'<a href="{html.escape(url, quote=True)}">{_tag_label(tag, linked=True)}</a>'


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


def format_author_line(parse_result: AnyParseResult) -> str:
    """作者行 (markdown): ``**<a>名字</a> <sub>@handle</sub>**`` (常规小字, 非等宽)。

    拼装与链接一律交给库里的 ``format_author_link`` —— 这里不要再手写一遍替换,
    否则"作者长什么样"这件事就有了两份实现 (曾经就是如此)。

    **不加冒号** (用户要求「取消冒号」) —— 名字已经可点、@handle 已是小字标识,
    再加冒号只是多余的标点。
    """
    label = format_author_link(
        get_parse_author_name(parse_result),
        str(getattr(parse_result, "author_handle", "") or ""),
        str(getattr(parse_result, "author_url", "") or ""),
    )
    if not label:
        return ""
    # **粗体只包名字, 不包 @handle**: 整行包 `**` 时角标里的 handle 会**继承粗体**
    # (服务端块实测是 textSubscript(textBold(textPlain))) —— 用户要的"常规样式"是
    # 名字粗体 + handle 常规小字。所以把 `**` 收在链接结束标签处。
    if "</a>" in label:
        head, _, tail = label.partition("</a>")
        return f"**{head}</a>**{tail}"
    return f"**{label}**"


_QUOTE_BLOCK_RE = re.compile(r"(?m)^>[^\n]*(?:\n>[^\n]*)*")

# 折叠阈值: 超过 500 字 (或行数超限) 就折起来。
# **正文与引用块共用这一套** —— 只写一份, 两处共用 (曾分别在两处硬编码, 改一处必漏另一处)。
# 200 对拉丁文字太紧: 英文 200 字符 ≈ 30 个词, 一个长句就顶到线 (实测一条三条短句的
# 英文推文 289 字符就被折了), 而中文 200 字是实打实的两三大段。放宽到 500 后
# 中英的信息量大致对齐。
_FOLD_CHAR_THRESHOLD = 500
_FOLD_LINE_THRESHOLD = 8


def _should_fold(text: str) -> bool:
    """文本是否超过折叠阈值 (字符数或内容行数任一超出)。

    **行数只数非空行**: 空行是段落排版, 不是内容量。若连空行一起数,
    「五句话 + 中间四个空行」= 9 行, 总共才十几个字也会被折叠 —— 行数阈值
    就成了比字符阈值更早触发的误判来源。
    """
    content_lines = sum(1 for line in text.splitlines() if line.strip())
    return len(text) > _FOLD_CHAR_THRESHOLD or content_lines > _FOLD_LINE_THRESHOLD

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
    """去掉引用块每行行首的 '>', 并中和块内文本自己的 markdown 定界符。

    中和是为了防止引用内容里的 ``*`` ``_`` 被 markdown 阶段误当定界符吃掉;
    斜体标记本身由 ``parsehub`` 的 ``format_quote_block`` 直接产出 ``<i>``,
    这里没有"把 ``*`` 转成 ``<i>``"这一步。
    """
    lines = block.rstrip("\n").split("\n")
    body = "\n".join(line[1:].lstrip() if line.startswith(">") else line for line in lines)
    return neutralize_markdown(body)


def _strip_quote_prefix(match: re.Match) -> str:
    return _strip_quote_markers(match.group(0))


#: 折叠块的默认摘要文案 (源码语言), 调用方按 locale 传译文进来
_DEFAULT_FOLD_SUMMARY = "展开全文"

#: 折叠前**留在外面**的预览: 折叠态只显示 summary, 不留预览的话正文一个字都看不到
#: (用户原话「这个折叠看不到一点内容啊」)。行数与字符数任一触顶即停。
_FOLD_PREVIEW_LINES = 2
_FOLD_PREVIEW_CHARS = 100


def split_fold_preview(content: str) -> tuple[str, str]:
    """把内容拆成 (外面可见的预览, 折进 details 的剩余部分)。

    预览取开头几行 (行数/字符数任一超限即停); 内容全挤在一行时按字符切,
    否则整条正文只有一行就永远折不起来。剩余为空时返回 (content, "") = 不折。
    """
    lines = content.split("\n")
    end = 0
    chars = 0
    for line in lines:
        if end >= _FOLD_PREVIEW_LINES or (end and chars + len(line) > _FOLD_PREVIEW_CHARS):
            break
        chars += len(line)
        end += 1

    preview = "\n".join(lines[:end]).rstrip()
    rest = "\n".join(lines[end:]).strip()

    # 只有一行 (或首行就吃满): 按字符切出预览, 否则没有可折的剩余
    if not rest and len(preview) > _FOLD_PREVIEW_CHARS:
        rest = preview[_FOLD_PREVIEW_CHARS:].strip()
        preview = preview[:_FOLD_PREVIEW_CHARS].rstrip()

    if not rest:
        return content, ""
    return preview, rest


def use_br_linebreaks(text: str) -> str:
    """把换行转成 ``<br>``。

    ``<blockquote expandable>`` 里**真换行会被并成空格** (实测), 只有 ``<br>``
    才保留换行; 而带真空行的内容会让它**退化成普通引用块、完全不折叠**。
    把换行写成 ``<br>`` 两个问题一起解决。
    """
    lines = [line.rstrip() for line in text.split("\n")]
    return "<br>".join(lines)


def render_expandable_quote(content: str) -> str:
    """引用块的老折叠形态: 整体一个 ``<blockquote expandable>``。

    与正文的 ``<details>`` 不同 —— ``<details>`` 会把「预览 / 按钮 / 折起部分」
    切成三段 (用户原话「引用块被按钮分割, 割裂感太强了」)。这里整块一起折,
    客户端自己显示开头几行, 形态是一体的。
    """
    return f"<blockquote expandable>{use_br_linebreaks(content)}</blockquote>"


#: 独占一行的分隔线 (markdown / discourse 的 hr)。
#: 服务端**要求前后有空行**才认它是分隔线: 紧贴上一行时会被当普通文本 (实测一篇 linux.do
#: 长帖里 18 处变成了字面 '---' 段落), 更糟的是紧跟文字行时会被当成 setext 标题语法
#: (把上一行整行变成 H2)。所以渲染前统一补空行。
_HR_LINE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _normalize_hr(text: str) -> str:
    """把独占一行的分隔线前后规范成空行, 让它稳定解析成分隔线。

    只动"整行只有分隔符"的行 —— ``**粗体**``、``***粗斜体***``、列表项里的短横线、
    表格的 ``|---|`` 都不是独占一行的纯分隔符, 不会被误伤。
    """
    lines = text.splitlines()
    if not any(_HR_LINE_RE.match(line.strip()) for line in lines):
        return text

    out: list[str] = []
    for line in lines:
        if _HR_LINE_RE.match(line.strip()):
            if out and out[-1].strip():
                out.append("")
            out.append("---")
            out.append("")
        else:
            out.append(line)
    # 补空行可能撑出连续空行: 合并回一个空行, 避免块之间出现大段空白
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _fold_whole(content: str, *, summary: str = "") -> str:
    """把**整段**内容包成一个可折叠块 (全收起, 不留预览)。

    与 ``_render_foldable`` 的区别: 那个会留开头几行当预览 —— 那适合"一段长正文",
    但整篇折叠时预览本身就是一大段内容, 而用户要的是"收起时一行展开全文"。
    """
    return f"<details><summary>{summary or _DEFAULT_FOLD_SUMMARY}</summary>\n\n{content}\n\n</details>"


def _render_foldable(content: str, *, summary: str = "") -> str:
    """包成可折叠块 (长内容展示用): 开头几行留外面当预览, 其余折进 details。

    **用 <details> 而不是 <blockquote expandable>**: 后者一旦块内含空行,
    Telegram 就把它退化成普通引用块 (完全不折叠) —— 而推文正文天然是多段落,
    于是长正文永远折不起来; 而且引用块内的换行会被并成空格, 段落结构全丢。
    <details> 保留完整段落, 是富文本 markdown 里唯一能折叠多段落的容器。

    但 <details> 收起时**只显示 summary**, 所以正文得留一截在外面当预览,
    否则用户看到的就是光秃秃一个「展开全文」。
    """
    preview, rest = split_fold_preview(content)
    if not rest:
        return content
    folded = f"<details><summary>{summary or _DEFAULT_FOLD_SUMMARY}</summary>\n\n{rest}\n\n</details>"
    return f"{preview}\n\n{folded}"


def convert_markdown_quote(
    text: str, *, allow_blockquote: bool = True, allow_expandable: bool = True, fold_summary: str = ""
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
            return render_expandable_quote(body)
        return f"<blockquote>{body}</blockquote>"

    return _QUOTE_BLOCK_RE.sub(_replace, text)


def format_text(
    text: str,
    *,
    allow_blockquote: bool = True,
    allow_expandable: bool = True,
    max_length: int | None = None,
    fold_summary: str = "",
) -> str:
    """格式化输出内容, 按需限制长度, 添加折叠块样式。

    **折叠只做一次, 对象是整段内容** (用户要求「整篇只折叠一次」)。

    以前是按引用块把正文切成多段、**每段各自判断**是否超阈值 —— 对引用多的长帖
    (discourse 的楼层引用动辄十几个) 会切出十几个片段, 于是产生一堆「展开全文」按钮
    (实测一篇 linux.do 长帖出了 7 个), 碎得没法读。现在改成: 整段超阈值就整段折一次,
    引用块不再单独折叠 (也不做"折叠里再折叠"——客户端对嵌套折叠没有保证)。

    :param max_length: 超过就截断 (在 markdown 阶段截, 避免切断后面生成的 blockquote 标签)。
        **默认不截断** —— 让"忘了传"时的行为是安全的 (完整渲染), 而不是静默砍掉内容。
        只有**发文件**的路径需要传 (Telegram 的媒体 caption 上限 1024):
        ``send_raw`` / ``send_zip`` / GIF 过多时的纯文字提示。
        富文本正文没有这个限制, 不传即可 (超长靠折叠收起)。
    """
    # 分隔线先规范化: 原文里紧贴上一行的 '---' 在服务端不会被认成分隔线
    text = _normalize_hr(text.strip())
    if max_length is not None and len(text) > max_length:
        text = text[: max_length - 100] + "......"

    if not allow_blockquote:
        # 该通道不支持引用块: 剥掉前缀后按普通文本处理
        text = _QUOTE_BLOCK_RE.sub(_strip_quote_prefix, text)
        if allow_expandable and _should_fold(text):
            return _fold_whole(text, summary=fold_summary)
        return text

    # 先按"引用块不折叠"把引用块转换好, 再拿**整篇**判断是否要折。
    # 引用块的长度算在整篇里, 所以"整篇没超阈值、单个引用块却超"不可能出现 ——
    # 整篇折叠是唯一的折叠点 (也就不会有嵌套折叠)。
    converted = convert_markdown_quote(text, allow_expandable=False, fold_summary=fold_summary)
    if allow_expandable and _should_fold(converted):
        return _fold_whole(converted, summary=fold_summary)
    return converted


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
    """HTML 粗体的阶段标签 —— 给 **老 caption 路径** (HTML parse mode) 用。"""
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
