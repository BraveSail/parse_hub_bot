"""富文本 inline 的二次替换: 选中后把占位消息编辑成带媒体的完整富文本。

inline **回答查询时**不能带外部媒体 (Telegram 直接回 `EXTERNAL_MEDIA_NOT_SUPPORTED`),
所以占位结果只发文字; 用户选中后再走「下载 → 上传 → 编辑」: 用
`messages.EditInlineBotMessage(rich_message=...)` 把同一条消息换成带媒体的富文本。
这条编辑路径的媒体字段要的是**已上传的** photo/document (pyrogram 的
`InputRichMessageMedia.write` 内部用 `messages.UploadMedia`, peer=self)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram import raw, utils
from pyrogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    InputRichMessage,
    InputRichMessageMedia,
)

from log import logger

if TYPE_CHECKING:
    from pyrogram import Client

    from services.media import ProcessedMedia

logger = logger.bind(name="InlineRichEdit")

#: 富文本结果项的 result_id (选中回调靠它区分)
RICH_RESULT_ID = "rich"

#: 媒体类型 -> tg:// 占位符的 kind
_KIND_BY_CLASS = {
    "ImageFile": "photo",
    "VideoFile": "video",
    "LivePhotoFile": "video",
    "AniFile": "video",
}


def build_media_items(
    processed_list: list[ProcessedMedia],
    media_refs: list,
    *,
    is_sensitive: bool = False,
) -> tuple[list[InputRichMessageMedia], list[str], dict]:
    """把已下载的媒体构造成富文本 media 块 + 正文占位符 + (打码用的) blocks 映射。"""
    from plugins.parse.rich_blocks import SpoilerPhotoBlock, SpoilerVideoBlock

    media: list[InputRichMessageMedia] = []
    placeholders: list[str] = []
    media_blocks: dict = {}
    index = 0

    for media_ref, processed in zip(media_refs, processed_list, strict=False):
        file_paths = processed.output_paths or [processed.source.path]
        kind = _KIND_BY_CLASS.get(type(processed.source).__name__)
        if kind is None:
            continue
        for file_path in file_paths:
            file_path_str = str(file_path)
            match type(processed.source).__name__:
                case "ImageFile":
                    item = InputMediaPhoto(file_path_str, has_spoiler=is_sensitive)
                case "AniFile":
                    item = InputMediaAnimation(file_path_str, has_spoiler=is_sensitive)
                case "VideoFile" | "LivePhotoFile":
                    item = InputMediaVideo(
                        file_path_str,
                        has_spoiler=is_sensitive,
                        video_cover=getattr(media_ref, "thumb_url", None),
                        supports_streaming=True,
                    )
                case _:
                    continue
            media_id = f"m{index}"
            index += 1
            media.append(InputRichMessageMedia(media_id, item))
            placeholders.append(f"![](tg://{kind}?id={media_id})")
            match kind:
                case "photo":
                    media_blocks[media_id] = SpoilerPhotoBlock(item, spoiler=is_sensitive)
                case _:
                    media_blocks[media_id] = SpoilerVideoBlock(item, spoiler=is_sensitive)
    return media, placeholders, media_blocks


async def edit_inline_rich_message(
    cli: Client,
    inline_message_id: str,
    *,
    markdown: str,
    media: list[InputRichMessageMedia] | None = None,
    blocks: list | None = None,
) -> bool:
    """把一条 inline 消息编辑成富文本 (可带媒体)。

    媒体会在这一步真正上传 (peer=self), 所以调用方要保证文件还在本地。
    返回是否成功 —— **返回值不能当成功判据**, 用户看到的消息才是。

    ``blocks`` 非空时走 blocks 路径: 官方 API 的富文本媒体块没有 spoiler 字段,
    敏感内容只有 raw 的 ``PageBlockPhoto/Video(spoiler=True)`` 才能打码。
    """
    unpacked = utils.unpack_inline_message_id(inline_message_id)
    session = await cli.get_session(unpacked.dc_id, is_media=True)
    if blocks:
        rich = InputRichMessage(blocks=blocks)
        # blocks 里的媒体由各 block 自己上传 (_get_input_photo/Document, peer=self)
        raw_rich = await rich.write(client=cli, chat_id=None)
    else:
        rich = InputRichMessage(markdown=markdown, media=media or None)
        # 走 InputRichMessage.write: 它负责把媒体上传/复用成 InputRichFile*
        raw_rich = await rich.write(client=cli, chat_id=None)
    await session.invoke(
        raw.functions.messages.EditInlineBotMessage(
            id=unpacked,
            rich_message=raw_rich,
            reply_markup=raw.types.ReplyKeyboardHide(),
        )
    )
    return True


def cache_media_blocks(entry) -> tuple[list, list[str]]:
    """把缓存里的 file_id 变成富文本的 media 块 + markdown 占位符。

    用已存在的 file_id 走 ``InputMediaPhoto/Document``: pyrogram 的 ``_get_input_photo``
    遇到 ``InputMediaPhoto`` 直接返回它的 id, **不触发任何上传**, 所以是即时可用的。
    """
    from pyrogram.types import (
        InputMediaAnimation,
        InputMediaDocument,
        InputMediaPhoto,
        InputMediaVideo,
    )

    from services.cache import CacheMediaType

    media: list[InputRichMessageMedia] = []
    placeholders: list[str] = []
    for index, m in enumerate(entry.media or []):
        match m.type:
            case CacheMediaType.PHOTO:
                kind, item = "photo", InputMediaPhoto(m.file_id)
            case CacheMediaType.VIDEO:
                kind, item = "video", InputMediaVideo(m.file_id)
            case CacheMediaType.ANIMATION:
                kind, item = "video", InputMediaAnimation(m.file_id)
            case CacheMediaType.DOCUMENT:
                kind, item = "document", InputMediaDocument(m.file_id)
            case _:
                continue
        media_id = f"m{index}"
        media.append(InputRichMessageMedia(media_id, item))
        placeholders.append(f"![](tg://{kind}?id={media_id})")
    return media, placeholders


def build_cached_rich_content(
    entry, raw_url: str, *, lang: str, config, view_label: str = "", custom_content: str = ""
) -> tuple[str, list[InputRichMessageMedia]]:
    """从缓存条目重建富文本正文与媒体块 (file_id 复用, 零上传)。

    **直发与 inline 共用这一条**, 免得两边各写一份排版:
    缓存里存的只有解析字段, 排版一律交给 ``build_rich_markdown_by_str``。
    """
    from plugins.helpers import build_rich_markdown_by_str, wrap_collage

    media, placeholders = cache_media_blocks(entry)
    markdown = build_rich_markdown_by_str(
        entry.parse_result.title,
        entry.parse_result.content,
        raw_url,
        config=config,
        lang=lang,
        view_label=view_label,
        author_name=entry.parse_result.author_name,
        author_handle=entry.parse_result.author_handle,
        author_url=entry.parse_result.author_url,
        published_at=entry.parse_result.published_at,
        view_count=entry.parse_result.view_count,
        tags=entry.parse_result.tags,
        custom_content=custom_content,
        media_placeholders=wrap_collage(placeholders),
    )
    return markdown, media


def extract_cache_media(rich_message) -> list:
    """从发送后返回的富文本消息里取出媒体 file_id (写回缓存用, 下次零上传)。

    blocks 路径与 markdown 路径返回的结构不同, 这里统一递归找 Photo/Video/Document。
    """
    from services.cache import CacheMedia, CacheMediaType

    found: list = []

    def walk(node) -> None:
        if node is None or isinstance(node, (str, int)):
            return
        name = type(node).__name__
        if name == "RichBlockPhoto":
            file_id = getattr(getattr(node, "photo", None), "file_id", None)
            if file_id:
                found.append(CacheMedia(type=CacheMediaType.PHOTO, file_id=file_id))
                return
        if name == "RichBlockVideo":
            video = getattr(node, "video", None)
            file_id = getattr(video, "file_id", None) or getattr(getattr(video, "document", None), "file_id", None)
            cover = getattr(getattr(node, "cover", None), "file_id", None)
            if file_id:
                found.append(CacheMedia(type=CacheMediaType.VIDEO, file_id=file_id, cover_file_id=cover))
                return
        for attr in ("blocks", "items"):
            value = getattr(node, attr, None)
            if value is None or isinstance(value, (str, int)):
                continue
            for child in value if isinstance(value, list) else [value]:
                walk(child)

    for block in getattr(rich_message, "blocks", None) or []:
        walk(block)
    return found


__all__ = [
    "RICH_RESULT_ID",
    "build_cached_rich_content",
    "build_media_items",
    "cache_media_blocks",
    "edit_inline_rich_message",
    "extract_cache_media",
]
