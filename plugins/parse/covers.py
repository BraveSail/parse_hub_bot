"""视频封面: 把平台的缩略图下载并缩放成 Telegram 认可的缩略图文件。

富文本路径只上传 document 本身 (``InputRichFileDocument`` 只有 id + document),
pyrogram 给 ``InputMediaVideo`` 的 ``video_cover`` 参数在 ``_get_input_document`` 里被丢弃,
所以**远端封面地址传不进去** —— 必须在本地准备好文件, 走 ``thumb=`` 上传。

Telegram 对缩略图的要求: JPEG、边长不超过 320、体积小于 200 KB。
平台的封面原图 (如 B 站的 1920x1080) 需要缩放压缩后才能用。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from parsehub.types import AniRef, LivePhotoRef, VideoRef
from parsehub.utils import http
from PIL import Image

from core import bs, pl_cfg
from log import logger
from utils.helpers import to_list

if TYPE_CHECKING:
    from parsehub.types import AnyMediaRef, Platform

logger = logger.bind(name="VideoCover")

#: Telegram 缩略图上限: 边长 320、体积 200 KB
_MAX_EDGE = 320
_MAX_BYTES = 200 * 1024
_QUALITY_LADDER = (85, 75, 65, 55, 45)


def _cover_dir() -> Path:
    """封面缓存目录 (与下载目录同级, 便于整体清理)。"""
    path = Path(bs.download_dir) / ".covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(url: str) -> Path:
    return _cover_dir() / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]}.jpg"


def shrink_to_thumbnail(data: bytes) -> bytes | None:
    """把图片字节压成 Telegram 缩略图 (≤320px, <200KB JPEG); 不是图片就返回 None。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((_MAX_EDGE, _MAX_EDGE))
            for quality in _QUALITY_LADDER:
                buffer = io.BytesIO()
                img.save(buffer, "JPEG", quality=quality, optimize=True)
                if buffer.tell() < _MAX_BYTES:
                    return buffer.getvalue()
            return None
    except Exception as e:
        logger.debug(f"封面压缩失败: {e}")
        return None


async def fetch_video_thumb(
    url: str,
    *,
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
) -> Path | None:
    """下载并缓存一张封面; 失败返回 None (调用方静默降级, 不能因为封面发不出消息)。"""
    if not url:
        return None
    target = _cache_path(url)
    if target.is_file() and target.stat().st_size > 0:
        return target

    try:
        async with http.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            logger.debug(f"封面下载失败: status={response.status_code} url={url[:90]}")
            return None
        thumb = shrink_to_thumbnail(response.content)
        if thumb is None:
            return None
        # 先写临时文件再改名, 避免并发下读到半个文件
        temp = target.with_suffix(".tmp")
        temp.write_bytes(thumb)
        temp.replace(target)
        logger.debug(f"封面已缓存: {len(thumb)} 字节 url={url[:80]}")
        return target
    except Exception as e:
        logger.debug(f"封面下载异常: {type(e).__name__} {e} url={url[:80]}")
        return None


async def prepare_video_thumbs(
    media_refs: Iterable[AnyMediaRef],
    *,
    platform: Platform | None = None,
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Path]:
    """给视频类媒体准备封面文件。

    封面走与媒体下载同一个代理 (平台的 downloader proxy), 免得同一条消息里
    视频能下、封面下不来。返回 ``{封面地址: 本地文件}``, 拿不到的条目缺席。
    """
    if proxy is None and platform is not None:
        proxy = pl_cfg.roll_downloader_proxy(platform.id)
    wanted: list[str] = []
    for ref in to_list(list(media_refs)):
        if not isinstance(ref, VideoRef | LivePhotoRef | AniRef):
            continue
        url = str(getattr(ref, "thumb_url", "") or "")
        if url and url not in wanted:
            wanted.append(url)
    if not wanted:
        return {}

    results = await asyncio.gather(
        *(fetch_video_thumb(url, proxy=proxy, headers=headers) for url in wanted)
    )
    return {url: path for url, path in zip(wanted, results, strict=True) if path is not None}


__all__ = ["fetch_video_thumb", "prepare_video_thumbs", "shrink_to_thumbnail"]
