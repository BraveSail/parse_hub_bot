"""富文本 inline 的二次替换: 选中后把占位消息编辑成带媒体的完整富文本。

inline **回答查询时**不能带外部媒体 (Telegram 直接回 `EXTERNAL_MEDIA_NOT_SUPPORTED`),
所以占位结果只发文字; 用户选中后再走「下载 → 上传 → 编辑」: 用
`messages.EditInlineBotMessage(rich_message=...)` 把同一条消息换成带媒体的富文本。
这条编辑路径的媒体字段要的是**已上传的** photo/document (pyrogram 的
`InputRichMessageMedia.write` 内部用 `messages.UploadMedia`, peer=self)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from parsehub.types import AniFile, AnyMediaRef, ImageFile, LivePhotoFile, VideoFile
from pyrogram import raw, utils
from pyrogram.types import (
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    InputRichBlockAnimation,
    InputRichMessage,
    InputRichMessageMedia,
)

from log import logger

if TYPE_CHECKING:
    from pyrogram import Client

    from services.media import ProcessedMedia

logger = logger.bind(name="InlineRichEdit")

#: 媒体类型 -> tg:// 占位符的 kind
RICH_MEDIA_KIND: dict[type, str] = {
    ImageFile: "photo",
    VideoFile: "video",
    LivePhotoFile: "video",
    AniFile: "video",
}

#: 富文本结果项的 result_id (选中回调靠它区分)
RICH_RESULT_ID = "rich"

def build_rich_media(
    media_refs: Sequence[AnyMediaRef],
    processed_list: list[ProcessedMedia],
    *,
    video_cover: bool = True,
    is_sensitive: bool = False,
    video_thumbs: Mapping[str, Path] | None = None,
    quoted_media_count: int = 0,
    reply_media_count: int = 0,
) -> tuple[list[InputRichMessageMedia], list[str], dict[str, Any], list[str], list[str]]:
    """把已下载的媒体构造成富文本 media 块 + 正文占位符 + (打码用的) blocks 映射。

    媒体用下载好的本地文件上传 (``send_rich_message`` 内部走 ``messages.UploadMedia``),
    不把原始 URL 交给 Telegram 抓取 —— 那条路有 20 MB 上限。

    直发与 inline 编辑**共用这一份**, 免得两边各写一遍。

    :param video_thumbs: 视频封面 URL -> 已下载好的本地图片 (见 ``covers.prepare_video_thumbs``)。
        必须是本地文件: 富文本路径只上传 document 本身, 远端 cover 参数会被丢弃。
    :param quoted_media_count: ``media_refs`` 末尾有多少个属于**被引用内容**。
    :param reply_media_count: 紧接在被引用媒体之前的多少个属于**被回复内容**。
        两者的占位符分别作为第四、五个返回值给出, 由调用方放进对应的引用块内部。
    :return: ``(media, placeholders, media_blocks, quoted_placeholders, reply_placeholders)``
    """
    from plugins.parse.rich_blocks import SpoilerPhotoBlock, SpoilerVideoBlock
    from services.media import resolve_media_info

    thumbs = video_thumbs or {}
    media: list[InputRichMessageMedia] = []
    placeholders: list[str] = []
    media_blocks: dict[str, Any] = {}
    index = 0
    # 每个 ref 产出的占位符 (一个 ref 可能展开成多个文件), 供末尾切分引用帖媒体
    per_ref: list[list[str]] = []

    for media_ref, processed in zip(media_refs, processed_list, strict=False):
        group: list[str] = []
        per_ref.append(group)
        file_paths = processed.output_paths or [processed.source.path]
        for file_path in file_paths:
            file_path_str = str(file_path)
            width, height, duration = resolve_media_info(processed, file_path_str)
            kind = RICH_MEDIA_KIND.get(type(processed.source))
            if kind is None:
                continue

            match processed.source:
                case ImageFile():
                    item = InputMediaPhoto(media=file_path_str, has_spoiler=is_sensitive)
                case AniFile():
                    item = InputMediaAnimation(media=file_path_str, has_spoiler=is_sensitive)
                case VideoFile():
                    thumb_url = str(getattr(media_ref, "thumb_url", "") or "")
                    item = InputMediaVideo(
                        media=file_path_str,
                        has_spoiler=is_sensitive,
                        thumb=thumbs.get(thumb_url),
                        video_cover=thumb_url if video_cover else None,
                        duration=duration,
                        width=width,
                        height=height,
                        supports_streaming=True,
                    )
                case LivePhotoFile():
                    # 实况照片本身就是一张图 + 一段视频, 用图片那半当封面最贴切
                    item = InputMediaVideo(
                        media=processed.source.video_path,
                        has_spoiler=is_sensitive,
                        thumb=thumbs.get(str(getattr(media_ref, "thumb_url", "") or "")),
                        video_cover=file_path_str if video_cover else None,
                        duration=duration,
                        width=width,
                        height=height,
                        supports_streaming=True,
                    )
                case _:
                    continue

            media_id = f"m{index}"
            index += 1
            media.append(InputRichMessageMedia(media_id, item))
            placeholder = f"![](tg://{kind}?id={media_id})"
            placeholders.append(placeholder)
            group.append(placeholder)
            # blocks 路径: 官方 API 的 InputRichBlockPhoto/Video 没有 spoiler, 自定义块才有
            match processed.source:
                case ImageFile():
                    media_blocks[media_id] = SpoilerPhotoBlock(item, spoiler=is_sensitive)
                case VideoFile() | LivePhotoFile():
                    media_blocks[media_id] = SpoilerVideoBlock(item, spoiler=is_sensitive)
                case AniFile():
                    media_blocks[media_id] = InputRichBlockAnimation(item)

    # refs 顺序是 [正文..., 被回复..., 被引用...]: 从末尾往前切出两个引用块
    quoted_count = max(0, min(quoted_media_count, len(per_ref)))
    tail_split = len(per_ref) - quoted_count
    reply_count = max(0, min(reply_media_count, tail_split))
    head_split = tail_split - reply_count

    def flat(groups: list[list[str]]) -> list[str]:
        return [p for ref_group in groups for p in ref_group]

    return (
        media,
        flat(per_ref[:head_split]),
        media_blocks,
        flat(per_ref[tail_split:]),
        flat(per_ref[head_split:tail_split]),
    )


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


def cache_media_blocks(entry) -> tuple[list, list[str], list[str], list[str]]:
    """把缓存里的 file_id 变成富文本的 media 块 + 正文/被引用/被回复三级占位符。

    用已存在的 file_id 走 ``InputMediaPhoto/Document``: pyrogram 的 ``_get_input_photo``
    遇到 ``InputMediaPhoto`` 直接返回它的 id, **不触发任何上传**, 所以是即时可用的。

    缓存的媒体是平铺的媒体项列表, 顺序与 ``media_refs`` 一致
    (``[正文..., 被回复..., 被引用...]``), 按两个计数从末尾往前划给对应的引用块。
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

    pr = entry.parse_result
    quoted_count = max(0, min(int(getattr(pr, "quoted_media_count", 0) or 0), len(placeholders)))
    tail_split = len(placeholders) - quoted_count
    reply_count = max(0, min(int(getattr(pr, "reply_media_count", 0) or 0), tail_split))
    head_split = tail_split - reply_count
    return media, placeholders[:head_split], placeholders[tail_split:], placeholders[head_split:tail_split]


def rich_cache_entry(
    parse_result,
    media: list,
    *,
    quoted_media_count: int = 0,
    reply_media_count: int = 0,
):
    """把一次富文本发送的字段与媒体 file_id 收成缓存条目。

    ``quoted_media_count`` 记的是**媒体项数** (缓存里平铺的条数), 不是 ref 数
    —— 一个 ref 可能展开成多个文件, 缓存只能按项切分。

    放在这里而不是发送层: 私聊/群 (``sender``) 与 guest 都要写同一种条目。
    """
    # 两个都是 bot 侧模块, 函数内延迟导入避免循环依赖 (与上面的 helper 同一手法)
    from plugins.helpers import get_parse_author_name
    from services.cache import CacheEntry, CacheParseResult

    return CacheEntry(
        parse_result=CacheParseResult(
            title=parse_result.title,
            content=parse_result.content,
            author_name=get_parse_author_name(parse_result),
            author_handle=getattr(parse_result, "author_handle", ""),
            author_url=getattr(parse_result, "author_url", ""),
            is_sensitive=parse_result.is_sensitive,
            published_at=getattr(parse_result, "published_at", None),
            view_count=getattr(parse_result, "view_count", None),
            like_count=getattr(parse_result, "like_count", None),
            tags=list(getattr(parse_result, "tags", None) or []),
            quoted_media_count=quoted_media_count,
            reply_media_count=reply_media_count,
        ),
        media=media or None,
        rich=True,
    )


def build_cached_rich_content(
    entry, raw_url: str, *, lang: str, config, view_label: str = "", custom_content: str = ""
) -> tuple[str, list[InputRichMessageMedia]]:
    """从缓存条目重建富文本正文与媒体块 (file_id 复用, 零上传)。

    **直发与 inline 共用这一条**, 免得两边各写一份排版:
    缓存里存的只有解析字段, 排版一律交给 ``build_rich_markdown_by_str``。
    """
    from plugins.helpers import build_rich_markdown_by_str, wrap_collage

    media, placeholders, quoted_placeholders, reply_placeholders = cache_media_blocks(entry)
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
        like_count=entry.parse_result.like_count,
        tags=entry.parse_result.tags,
        custom_content=custom_content,
        media_placeholders=wrap_collage(placeholders),
        quote_media_placeholders=quoted_placeholders,
        reply_media_placeholders=reply_placeholders,
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
    "RICH_MEDIA_KIND",
    "RICH_RESULT_ID",
    "build_cached_rich_content",
    "build_rich_media",
    "cache_media_blocks",
    "edit_inline_rich_message",
    "extract_cache_media",
]
