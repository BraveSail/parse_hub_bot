"""富文本 blocks 路径: 敏感内容需要媒体打码时用它替代 markdown 路径。

为什么需要: 官方 Bot API 的 `InputRichBlockPhoto` 只有 photo/caption, **没有 spoiler**,
而 raw 的 `PageBlockPhoto` / `PageBlockVideo` 有 —— 所以「富文本 + 打码」只能自己构造
blocks。markdown 路径 (`InputRichBlockPhoto`) 打不了码, 敏感内容走那条路会泄露。

这里的转换器只处理我们自己产出的富文本正文结构 (标题/引用/列表/代码块/分隔线/页脚/媒体
占位), 行内格式解析 **粗体/斜体/行内代码/链接/删除线**。
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pyrogram import raw
from pyrogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockCollage,
    InputRichBlockDetails,
    InputRichBlockDivider,
    InputRichBlockExpandableBlockQuotation,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPreformatted,
    InputRichBlockSectionHeading,
)
from pyrogram.types.input_content.input_rich_block import (
    InputRichBlock,
    _get_input_document,
    _get_input_photo,
    _write_caption,
)
from pyrogram.types.messages_and_media.rich_text import (
    RichTextBold,
    RichTextCode,
    RichTextDateTime,
    RichTextItalic,
    RichTextStrikethrough,
    RichTextSubscript,
    RichTextSuperscript,
    RichTextUrl,
)

if TYPE_CHECKING:
    from pyrogram import Client
    from pyrogram.types import InputMediaPhoto, InputMediaVideo

logger = __import__("loguru").logger.bind(name="RichBlocks")


class SpoilerPhotoBlock(InputRichBlock):
    """等价于 pyrogram 的 InputRichBlockPhoto, 外加 spoiler (打码)。"""

    def __init__(self, photo: InputMediaPhoto, *, spoiler: bool = True, caption=None) -> None:
        super().__init__()
        self.photo = photo
        self.caption = caption
        self.spoiler = spoiler

    async def write(
        self, *, client: Client, chat_id: int | str | None = None, photos: list, documents: list
    ) -> raw.base.PageBlock:
        input_media = await self.photo.write(client=client, chat_id=chat_id)
        input_photo = await _get_input_photo(client, chat_id=chat_id, input_media=input_media)
        photos.append(input_photo)
        return raw.types.PageBlockPhoto(
            photo_id=input_photo.id,
            caption=await _write_caption(client, caption=self.caption),
            spoiler=self.spoiler,
        )


class SpoilerVideoBlock(InputRichBlock):
    """等价于 pyrogram 的 InputRichBlockVideo, 外加 spoiler (打码)。"""

    def __init__(self, video: InputMediaVideo, *, spoiler: bool = True, caption=None) -> None:
        super().__init__()
        self.video = video
        self.caption = caption
        self.spoiler = spoiler

    async def write(
        self, *, client: Client, chat_id: int | str | None = None, photos: list, documents: list
    ) -> raw.base.PageBlock:
        input_media = await self.video.write(client=client, chat_id=chat_id)
        input_document = await _get_input_document(client, chat_id=chat_id, input_media=input_media)
        documents.append(input_document)
        return raw.types.PageBlockVideo(
            video_id=input_document.id,
            caption=await _write_caption(client, caption=self.caption),
            spoiler=self.spoiler,
        )


# ── 行内格式 ──────────────────────────────────────────────────

#: 时间戳实体: ``<tg-time unix="…" format="…">显示文字</tg-time>``。
#:
#: **markdown 路径由服务端解析这个标签, blocks 路径必须自己认** —— 不认的话它会
#: 原样进 ``InputRichBlockFooter``, 用户看到的是字面的 ``<tg-time unix=…>``
#: (敏感内容走 blocks, 所以只有敏感推文的页脚会这样)。
_TIME_TAG_RE = re.compile(r'<tg-time\s+unix="(\d+)"(?:\s+format="([^"]*)")?\s*>(.*?)</tg-time>', re.S)

_INLINE_PATTERNS: list[tuple[re.Pattern, type]] = [
    # footer 里的来源链接用的是 HTML (markdown 链接语法在 footer 块里不解析)
    (re.compile(r'<a\s+href="([^"]+)"\s*>(.*?)</a>', re.S), RichTextUrl),
    (_TIME_TAG_RE, RichTextDateTime),
    # **footer 块里只有 HTML 标签解析, markdown 的 ** 会字面显示** (实测) ——
    # 阶段标签 (▎上传中...) 与引用块的斜体都写在 footer/正文里用 HTML 标签,
    # 所以 blocks 路径必须认它们 (不认就字面显示 "<b>"/"<i>")
    (re.compile(r"<b>(.*?)</b>", re.S), RichTextBold),
    (re.compile(r"<i>(.*?)</i>", re.S), RichTextItalic),
    # 作者行的 @用户名 = <sub><code>…</code></sub>; 块内引用块的署名也用它们
    (re.compile(r"<sub>(.*?)</sub>", re.S), RichTextSubscript),
    (re.compile(r"<sup>(.*?)</sup>", re.S), RichTextSuperscript),
    (re.compile(r"<code>(.*?)</code>", re.S), RichTextCode),
    (re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)"), RichTextUrl),  # [文字](链接)
    (re.compile(r"\*\*([^*\n]+)\*\*"), RichTextBold),
    (re.compile(r"`([^`\n]+)`"), RichTextCode),
    (re.compile(r"~~([^~\n]+)~~"), RichTextStrikethrough),
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), RichTextItalic),
]


def parse_inline(text: str) -> str | list:
    """把一行 markdown 拆成 Richtext 片段 (无格式时直接返回原字符串)。

    只处理正文里常见的几种行内标记; 链接单独处理 url 参数。
    """
    if not text:
        return text
    parts: list = []
    pos = 0
    while pos < len(text):
        best = None
        for pattern, cls in _INLINE_PATTERNS:
            m = pattern.search(text, pos)
            if m and (best is None or m.start() < best[0].start()):
                best = (m, cls)
        if best is None:
            parts.append(text[pos:])
            break
        m, cls = best
        if m.start() > pos:
            parts.append(text[pos : m.start()])
        if cls is RichTextDateTime:
            # 组: 1=unix, 2=format(可缺), 3=显示文字。format 缺省时传 None
            # (服务端会按默认形态渲染)
            label = html.unescape(m.group(3))
            stamp = datetime.fromtimestamp(int(m.group(1)), tz=UTC)
            parts.append(RichTextDateTime(parse_inline(label), stamp, m.group(2) or None))
        elif cls is RichTextUrl:
            if m.re.pattern.startswith("<a"):
                url, label = m.group(1), m.group(2)  # <a href="url">label</a>
            else:
                label, url = m.group(1), m.group(2)  # [label](url)
            parts.append(RichTextUrl(parse_inline(html.unescape(label)), url=html.unescape(url)))
        else:
            parts.append(cls(parse_inline(m.group(1))))
        pos = m.end()

    if len(parts) == 1 and isinstance(parts[0], str):
        return html.unescape(parts[0])
    return parts


# ── 正文 → blocks ────────────────────────────────────────────

_MEDIA_PLACEHOLDER_RE = re.compile(r"^!\[\]\(tg://(?:photo|video)\?id=([\w-]+)\)$")


def markdown_to_blocks(markdown: str, *, media_blocks: dict[str, InputRichBlock] | None = None) -> list[InputRichBlock]:
    """把富文本 markdown 转成 blocks 列表。

    只覆盖我们自己生成的语法: 标题 / 段落 / 引用 / 列表 / 代码块 / 分隔线 / 页脚 / 媒体占位。
    媒体占位换成调用方给的 block (图片/视频, 可带 spoiler)。
    """
    media_blocks = media_blocks or {}
    blocks: list[InputRichBlock] = []
    lines = (markdown or "").split("\n")
    i = 0

    def flush_paragraph(buf: list[str]) -> None:
        if buf:
            text = "\n".join(buf).strip()
            if text:
                blocks.append(InputRichBlockParagraph(parse_inline(text)))
            buf.clear()

    paragraph: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行结束段落
        if not stripped:
            flush_paragraph(paragraph)
            i += 1
            continue

        # 媒体占位
        if m := _MEDIA_PLACEHOLDER_RE.match(stripped):
            flush_paragraph(paragraph)
            block = media_blocks.get(m.group(1))
            if block is not None:
                blocks.append(block)
            i += 1
            continue

        # ── 折叠容器 ──
        # **markdown 路径由服务端解析这两个标签, blocks 路径必须自己认** ——
        # 不认的话标签会原样进段落, 用户看到字面的 <details>, 里面的内容也全散架。
        # 敏感内容走 blocks, 所以只有敏感内容会这样 (与 <tg-time> 同一类坑)。
        if stripped.startswith("<details>"):
            flush_paragraph(paragraph)
            chunk: list[str] = []
            one_line = re.match(r"<details>(.*)</details>\s*$", stripped, re.S)
            if one_line:
                chunk.append(one_line.group(1))
                i += 1
            else:
                chunk.append(stripped[len("<details>") :])
                i += 1
                while i < len(lines) and "</details>" not in lines[i]:
                    chunk.append(lines[i])
                    i += 1
                if i < len(lines):
                    chunk.append(lines[i].split("</details>", 1)[0])
                    i += 1
            inner_raw = "\n".join(chunk)
            sm = re.search(r"<summary>(.*?)</summary>", inner_raw, re.S)
            summary = sm.group(1).strip() if sm else ""
            body = inner_raw[sm.end() :] if sm else inner_raw
            # 递归: 内部可能是段落/媒体/图集 —— 媒体占位符照样要换成打码块
            blocks.append(
                InputRichBlockDetails(
                    parse_inline(summary),
                    markdown_to_blocks(body, media_blocks=media_blocks),
                )
            )
            continue

        if stripped.startswith("<blockquote expandable>"):
            flush_paragraph(paragraph)
            one_line = re.match(r"<blockquote expandable>(.*)</blockquote>\s*$", stripped, re.S)
            content = (
                one_line.group(1)
                if one_line
                else stripped[len("<blockquote expandable>") :].rsplit("</blockquote>", 1)[0]
            )
            # 块内换行在源 markdown 里是 <br> (真换行会被并成空格) —— 转成真换行后交给
            # RichText, 服务端才会分行
            for br in ("<br>", "<br/>", "<br />"):
                content = content.replace(br, "\n")
            blocks.append(InputRichBlockExpandableBlockQuotation(parse_inline(content)))
            i += 1
            continue

        # 图集: <tg-collage> 包住的若干媒体占位
        if stripped.startswith("<tg-collage>"):
            flush_paragraph(paragraph)
            items: list = []
            i += 1
            while i < len(lines) and "</tg-collage>" not in lines[i]:
                inner = lines[i].strip()
                if m := _MEDIA_PLACEHOLDER_RE.match(inner):
                    block = media_blocks.get(m.group(1))
                    if block is not None:
                        items.append(block)
                i += 1
            i += 1  # 跳过 </tg-collage>
            if len(items) > 1:
                blocks.append(InputRichBlockCollage(items))
            else:
                blocks.extend(items)
            continue

        # 分隔线
        if stripped in ("---", "***", "___"):
            flush_paragraph(paragraph)
            blocks.append(InputRichBlockDivider())
            i += 1
            continue

        # 页脚
        if stripped.startswith("<footer>") and stripped.endswith("</footer>"):
            flush_paragraph(paragraph)
            blocks.append(InputRichBlockFooter(parse_inline(stripped[len("<footer>") : -len("</footer>")])))
            i += 1
            continue

        # 标题
        if hm := re.match(r"^(#{1,6})\s+(.*)$", stripped):
            flush_paragraph(paragraph)
            blocks.append(InputRichBlockSectionHeading(parse_inline(hm.group(2)), len(hm.group(1))))
            i += 1
            continue

        # 代码块
        if stripped.startswith("```"):
            flush_paragraph(paragraph)
            language = stripped[3:].strip() or None
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # 跳过结束的 ```
            blocks.append(InputRichBlockPreformatted("\n".join(code), language=language))
            continue

        # 引用块 (连续 > 行合并)
        if stripped.startswith(">"):
            flush_paragraph(paragraph)
            quoted: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            text = "\n".join(quoted).strip()
            blocks.append(InputRichBlockBlockQuotation([InputRichBlockParagraph(parse_inline(text))]))
            continue

        # 列表 (连续 - / * / 数字项合并成一个 list)
        if re.match(r"^\s*(?:[-*+]|\d+\.)\s+", line):
            flush_paragraph(paragraph)
            items: list[InputRichBlockListItem] = []
            while i < len(lines) and re.match(r"^\s*(?:[-*+]|\d+\.)\s+", lines[i]):
                item_text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", lines[i])
                checked: bool | None = None
                if cm := re.match(r"^\[([ xX])\]\s*(.*)$", item_text):
                    checked = cm.group(1).lower() == "x"
                    item_text = cm.group(2)
                items.append(
                    InputRichBlockListItem(
                        [InputRichBlockParagraph(parse_inline(item_text))],
                        has_checkbox=True if checked is not None else None,
                        is_checked=checked,
                    )
                )
                i += 1
            blocks.append(InputRichBlockList(items))
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph(paragraph)
    return blocks
