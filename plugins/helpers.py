"""plugins 共用的工具函数和数据类"""

import html
import re
from urllib.parse import urlsplit

from easy_ai18n import LocaleContent
from markdown import markdown
from parsehub import ParseHub, Platform
from parsehub.types import AnyParseResult, RichTextParseResult
from pyrogram import Client
from pyrogram.types import Message

from i18n import t_
from log import logger
from repo.settings import SettingsConfig
from utils.converter import clean_article_html
from utils.ph import Telegraph

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
    telegraph_url: str | None = None,
    *,
    custom_content: str = "",
    config: SettingsConfig,
    rich: bool = False,
    allow_blockquote: bool = True,
) -> str:
    return build_caption_by_str(
        parse_result.title,
        replace_url(parse_result.platform, parse_result.markdown_content)
        if rich and isinstance(parse_result, RichTextParseResult)
        else parse_result.content,
        parse_result.raw_url,
        telegraph_url,
        platform=parse_result.platform,
        hide_source=config.hide_source,
        custom_content=custom_content,
        author_name=get_parse_author_name(parse_result),
        hide_title=config.hide_title,
        hide_desc=config.hide_desc,
        rich=rich,
        allow_blockquote=allow_blockquote,
    )


def build_caption_by_str(
    title: str | None,
    content: str | None,
    raw_url: str,
    telegraph_url: str | None = None,
    *,
    platform: Platform | None = None,
    hide_source: bool = False,
    custom_content: str = "",
    author_name: str = "",
    hide_title: bool = False,
    hide_desc: bool = False,
    rich: bool = False,
    allow_blockquote: bool = True,
) -> str:
    """构建消息正文：标题 + 内容 + 来源链接"""
    title, content = title or "", content or ""
    if rich:
        body = f"### {title}\n\n <details><summary>📃</summary>\n\n{content}\n\n</details>"
    elif telegraph_url:
        label = neutralize_markdown((title or content[:15]).replace("\n", " ") or "-")
        body = f"**[{label}]({telegraph_url})**"
    else:
        parts = []
        if not hide_title and title:
            parts.append(f"**{neutralize_markdown(title)}**")
        if not hide_desc and content:
            # 正文里的 URL 也要中和: URL 中的 '__' 会被 markdown 解析插进 <i> 破坏链接
            parts.append(neutralize_markdown_urls(content))
        body = format_text(("\n\n".join(parts)).strip(), allow_blockquote=allow_blockquote)

    if author_name:
        label = neutralize_markdown(html.escape(author_name))
        body = f"<b>{label}:</b>\n\n{body}" if body else f"<b>{label}:</b>"

    if custom_content:
        body += f"\n\n{custom_content}"

    if hide_source:
        return body
    platform = platform or ParseHub().get_platform(raw_url)
    display = neutralize_markdown(html.escape(platform.display_name)) if platform else ""
    source = f"Source（{display}）" if platform else "Source"
    # href 里的 URL 必须中和: 否则 pyrogram 会把 URL 中的 '__' 解析成 <i> 塞进 href
    safe_url = neutralize_markdown(raw_url)
    return f"{body}\n\n{format_label(f"<a href='{safe_url}'>{source}</a>")}"


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


_QUOTE_BLOCK_RE = re.compile(r"(?m)^>[^\n]*(?:\n>[^\n]*)*")

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


def _strip_quote_prefix(match: re.Match) -> str:
    """去掉引用块每行行首的 '>', 并中和块内的 markdown 定界符。"""
    lines = match.group(0).rstrip("\n").split("\n")
    body = "\n".join(line[1:].lstrip() if line.startswith(">") else line for line in lines)
    return neutralize_markdown(body)


def convert_markdown_quote(text: str, *, allow_blockquote: bool = True) -> str:
    """把 Markdown 引用行 (以 '>' 开头) 转成 Telegram 的 <blockquote>。

    allow_blockquote=False 时把引用前缀整个去掉: 内联消息带 blockquote 实体时,
    Telegram 会丢掉整批格式、整条消息变成纯文本。注意不能只跳过 <blockquote> 标签,
    pyrogram 的 Markdown 解析器只要看到行首 '>' 就会重新生成 blockquote 实体,
    所以那条通道必须把 '>' 前缀也剥掉。
    """
    if not allow_blockquote:
        return _QUOTE_BLOCK_RE.sub(_strip_quote_prefix, text)

    def _replace(match: re.Match) -> str:
        return f"<blockquote>{_strip_quote_prefix(match)}</blockquote>"

    return _QUOTE_BLOCK_RE.sub(_replace, text)


def format_text(text: str, *, allow_blockquote: bool = True) -> str:
    """格式化输出内容, 限制长度, 添加折叠块样式"""
    text = text.strip()
    if len(text) > 1000:
        # 在 Markdown 阶段截断, 避免切断后面生成的 blockquote 标签
        text = text[:900] + "......"
    text = convert_markdown_quote(text, allow_blockquote=allow_blockquote)
    if len(text) > 500 or len(text.splitlines()) > 10:
        if "<blockquote>" in text or not allow_blockquote:
            # Telegram 不支持嵌套 blockquote; 不允许引用块时也不能用 expandable
            return text
        return f"<blockquote expandable>{text}</blockquote>"
    return text


async def create_telegraph_page(html_content: str, cli: Client, parse_result: AnyParseResult) -> str:
    """创建 Telegraph 页面，返回页面 URL"""
    logger.debug(f"创建 Telegraph 页面: title={parse_result.title}")
    me = await cli.get_me()
    page = await Telegraph().create_page(
        parse_result.title or "-",
        html_content=html_content,
        author_name=f"{me.full_name} | @{me.username}",
        author_url=parse_result.raw_url,
    )
    logger.debug(f"Telegraph 页面已创建: {page.url}")
    return page.url


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


async def create_richtext_telegraph(cli: Client, parse_result: RichTextParseResult) -> str:
    """将富文本解析结果转换为 Telegraph 页面，返回页面 URL"""
    logger.debug(f"富文本转 Telegraph: platform={parse_result.platform}, md_len={len(parse_result.markdown_content)}")
    md = replace_url(parse_result.platform, parse_result.markdown_content)
    html = clean_article_html(markdown(md))
    return await create_telegraph_page(html, cli, parse_result)


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
