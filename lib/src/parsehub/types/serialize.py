"""解析结果对象的**无损往返**（给缓存用）。

为什么不直接拿 ``to_dict()`` 往返:

1. **媒体类型会丢**。``to_dict()`` 用 ``asdict`` 序列化媒体, 而 ``MediaRef`` 子类
   **没有类型判别字段** —— ``VideoRef`` 与 ``AniRef`` 的字段集几乎相同 (都带 ``duration``),
   反推不出类型。天真往返会把动图当视频发出去, **静默出错**。这里给媒体项加 ``kind``。
2. ``to_dict()`` 是**公开输出格式** (CLI 输出、结果落盘 JSON), 且被既有测试逐字段冻住,
   不该为了缓存改它。
3. ``RichTextParseResult.content`` 是**派生属性** (由 ``markdown_content`` 算出来),
   重建时不能回填 ``content`` —— ``__init__`` 会覆盖掉它 (不报错, 静默不一致)。
4. ``raw_url`` 不在构造参数里, 必须重建后单独赋值。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .media_ref import AniRef, ImageRef, LivePhotoRef, MediaRef, VideoRef
from .platform import Platform
from .post import PostType
from .result import (
    AnyParseResult,
    ImageParseResult,
    MultimediaParseResult,
    RichTextParseResult,
    VideoParseResult,
)

#: 媒体类型标签 —— **序列化必须显式带上**, 否则重建时区分不了 (见模块 docstring)
_KIND_TO_CLASS: dict[str, type[MediaRef]] = {
    "video": VideoRef,
    "image": ImageRef,
    "animation": AniRef,
    "live_photo": LivePhotoRef,
}
_CLASS_TO_KIND = {cls: kind for kind, cls in _KIND_TO_CLASS.items()}

_TYPE_TO_CLASS: dict[str, type] = {
    PostType.VIDEO.value: VideoParseResult,
    PostType.IMAGE.value: ImageParseResult,
    PostType.MULTIMEDIA.value: MultimediaParseResult,
    PostType.RICHTEXT.value: RichTextParseResult,
}


def _platform_from_id(platform_id: str) -> Platform | None:
    """按**短名**找平台枚举。

    ⚠️ 不能用 ``Platform(platform_id)``: 这个枚举每个成员的 value 是
    ``(短名, 显示名)`` 这样的 **tuple** (自定义 ``__init__`` 把短名存进 ``.id``),
    所以按 value 构造永远失败 —— 而失败是静默的 (platform 变 None, 页脚来源消失)。
    """
    return next((platform for platform in Platform if platform.id == platform_id), None)


def _media_to_dict(ref: MediaRef) -> dict[str, Any]:
    if (kind := _CLASS_TO_KIND.get(type(ref))) is None:
        raise TypeError(f"未知媒体类型, 无法序列化: {type(ref).__name__}")
    return {**asdict(ref), "kind": kind}


def _media_from_dict(data: dict[str, Any]) -> MediaRef:
    payload = dict(data)
    kind = payload.pop("kind", None)
    if kind is None:
        # 宁可响亮地失败, 也不猜 —— 猜错会把动图/实况照片当视频静默发错
        raise ValueError(f"缓存里的媒体缺少类型标记 (kind): {sorted(data)}")
    try:
        cls = _KIND_TO_CLASS[kind]
    except KeyError as e:
        raise ValueError(f"缓存里的媒体类型不认识: {kind}") from e
    return cls(**payload)


def _media_payload(media: Any) -> Any:
    """把 ``to_dict`` 风格的 media 字段还原成 ref 对象 (保持单个/列表的形态)。"""
    if media is None:
        return None
    if isinstance(media, list):
        return [_media_from_dict(item) for item in media]
    return _media_from_dict(media)


def _media_to_payload(media: Any) -> Any:
    """序列化 media 字段 (保持单个/列表的形态 —— 形态影响渲染)。"""
    if media is None:
        return None
    if isinstance(media, MediaRef):
        return _media_to_dict(media)
    if isinstance(media, Sequence) and not isinstance(media, str):
        return [_media_to_dict(item) for item in media]
    # 既不是 ref 也不是序列: 说明有平台塞了别的东西。这里不猜 (猜错就是静默发错媒体)
    raise TypeError(f"media 不是媒体对象也不是序列, 无法序列化: {type(media).__name__}")


def _as_list(payload: Any) -> list[Any]:
    """``ImageParseResult(photo=...)`` 内部要迭代, 单值必须包成列表。"""
    if payload is None:
        return []
    return payload if isinstance(payload, list) else [payload]


def result_to_cache_dict(result: AnyParseResult) -> dict[str, Any]:
    """结果对象 → 可 JSON 化的字典 (媒体带 ``kind``)。"""
    return {**result.to_dict(), "media": _media_to_payload(result.media)}


def result_from_cache_dict(data: dict[str, Any]) -> AnyParseResult:
    """字典 → 结果对象。``result_to_cache_dict`` 的逆运算。"""
    try:
        cls = _TYPE_TO_CLASS[data["type"]]
    except KeyError as e:
        raise ValueError(f"缓存里的结果类型不认识: {data.get('type')!r}") from e

    media = _media_payload(data.get("media"))
    published_at = data.get("published_at")
    common: dict[str, Any] = {
        "title": data.get("title", ""),
        "author_name": data.get("author_name", ""),
        "is_sensitive": bool(data.get("is_sensitive", False)),
        "published_at": datetime.fromisoformat(published_at) if published_at else None,
        "view_count": data.get("view_count"),
        "like_count": data.get("like_count"),
        "author_handle": data.get("author_handle", ""),
        "author_url": data.get("author_url", ""),
        "tags": data.get("tags") or [],
        "quoted_media_count": data.get("quoted_media_count", 0),
        "reply_media_count": data.get("reply_media_count", 0),
    }

    # content 只在**非 RichText** 时回填 —— 那个类的 content 是派生属性
    # (由 markdown_content 算), 喂回去会被 __init__ 静默覆盖
    content = data.get("content", "")

    match cls:
        case _ if cls is VideoParseResult:
            # 视频是唯一可能是**单个** ref 的类型
            result = VideoParseResult(video=media, content=content, **common)
        case _ if cls is ImageParseResult:
            result = ImageParseResult(photo=_as_list(media), content=content, **common)
        case _ if cls is RichTextParseResult:
            result = RichTextParseResult(
                media=media,
                markdown_content=data.get("markdown_content", ""),
                **common,
            )
        case _:
            result = MultimediaParseResult(media=media, content=content, **common)

    # raw_url 不在构造参数里 (基类里初始为空串), 重建后单独赋回
    result.raw_url = data.get("raw_url", "")
    if (platform_id := data.get("platform")) is not None:
        # 平台枚举改名/移除时 (老缓存) 留 None 而不是抛异常 —— 渲染退化成"拿不到来源"
        result.platform = _platform_from_id(platform_id)
    return result


__all__ = ["result_from_cache_dict", "result_to_cache_dict"]
