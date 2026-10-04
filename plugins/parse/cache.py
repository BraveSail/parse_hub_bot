"""从已发送的消息里提取 file_id, 供缓存复用。

只保留从 Message 提取 (``cache_media_from_message``); 从 file_id **构建**发送
用的 media group 已由 ``plugins.parse.inline_rich.cache_media_blocks``
负责 —— 富文本路径统一走它, 旧 caption 路径的 ``build_cached_media_group``
早已无人调用, 2026-10-04 删除。
"""

from pyrogram.types import Message

from services import CacheMedia, CacheMediaType


def cache_media_from_message(m: Message) -> CacheMedia | None:
    """从已发送的 Telegram Message 提取 CacheMedia。"""
    if m.photo:
        return CacheMedia(type=CacheMediaType.PHOTO, file_id=m.photo.file_id)
    if m.video:
        return CacheMedia(
            type=CacheMediaType.VIDEO,
            file_id=m.video.file_id,
            cover_file_id=m.video.video_cover.file_id if m.video.video_cover else None,
        )
    if m.animation:
        return CacheMedia(type=CacheMediaType.ANIMATION, file_id=m.animation.file_id)
    if m.document:
        return CacheMedia(type=CacheMediaType.DOCUMENT, file_id=m.document.file_id)
    return None
