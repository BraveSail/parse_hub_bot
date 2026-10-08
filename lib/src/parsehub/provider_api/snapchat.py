"""Snapchat Spotlight 自研 provider —— 从页面 ``__NEXT_DATA__`` 里取明文直链。

spotlight 页面是 Next.js 的 SSR 页，**匿名可访问、无需 cookie**（实测 200）。
当前视频的媒体直链就在页面的内联 JSON 里，形态固定::

    props.pageProps.spotlightFeed.spotlightStories[]
        └─ 按 ``item.story.storyId.value == <视频ID>`` 选中本条目
           └─ item.metadata
                ├─ videoMetadata
                │    ├─ contentUrl   ← **明文 https 直链**（cf-st.sc-cdn.net/…），无签名/无解密
                │    ├─ name / description / thumbnailUrl / uploadDateMs
                │    ├─ durationMs / width / height / viewCount / shareCount
                │    └─ creator.personCreator.{username,name,url}
                ├─ description       ← 视频级文案（Spotlight 常为空串）
                └─ hashtags          ← 标签
    props.pageProps.videoMetadata   ← feed 缺失时的兜底（与当前视频同一份元数据）

直链形如 ``https://cf-st.sc-cdn.net/d/<id>.1034.IRZXSOY?mo=…&uc=46``，是**明文 https**，
可直接交给项目自带下载器（``utils.downloader``），不需要 yt-dlp。

⚠️ 关于 ``description``：实测 ``videoMetadata.description`` 是 Snapchat 的**固定模板**
（同页 8/8 条目都等于 "Another Spotlight Snap brought to you by Snapchat"），**不是**文案，
所以不当正文用；正文取**视频级** ``metadata.description``（Spotlight 通常为空，拿不到就留空）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..utils import http
from ..utils.helpers import UA, to_datetime, to_int


class SnapchatError(Exception):
    """Snapchat 页面解析相关错误（网络/结构不符/字段缺失）。"""


#: Next.js 的 ``__NEXT_DATA__`` 内联 JSON —— 标签形态固定（id + JSON 正文），
#: 用 re 抠出正文再 ``json.loads`` 即可，不必拉 HTML 解析器。
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

#: 视频 ID 的两种出现形态: ``/spotlight/<id>`` 与 ``/@user/<id>``。
_VIDEO_ID_PATTERNS = (
    re.compile(r"/spotlight/([A-Za-z0-9_-]+)"),
    re.compile(r"/@[^/]+/([A-Za-z0-9_-]+)"),
)


def get_video_id(url: str) -> str:
    """从链接里取视频 ID (即 ``storyId``)。

    支持 ``snapchat.com/spotlight/<id>`` 与 ``snapchat.com/@user[/spotlight]/<id>``。
    """
    for pattern in _VIDEO_ID_PATTERNS:
        if match := pattern.search(url or ""):
            return match.group(1)
    path = urlparse(url or "").path.rstrip("/")
    if path and (candidate := path.rsplit("/", 1)[-1]):
        return candidate
    raise SnapchatError(f"无法从链接中获取视频 ID: {url}")


def extract_next_data(html: str) -> dict[str, Any]:
    """从页面 HTML 里抠出 ``__NEXT_DATA__`` 的 JSON（纯函数，便于离线测试）。"""
    match = _NEXT_DATA_RE.search(html or "")
    if match is None:
        raise SnapchatError("页面中没有 __NEXT_DATA__")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise SnapchatError(f"__NEXT_DATA__ 不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise SnapchatError("__NEXT_DATA__ 不是 JSON 对象")
    return data


def _select_story_metadata(next_data: dict[str, Any], video_id: str) -> dict[str, Any]:
    """在页面数据里选出目标视频的 ``metadata`` 节点。

    优先按 ``spotlightStories[].story.storyId.value`` 精确匹配；feed 整体缺失时
    退回页面顶层的 ``videoMetadata``。feed 存在但 matched 不到该 ID ⇒ 报错，**不**
    悄悄退回另一个视频的元数据。
    """
    page_props = ((next_data.get("props") or {}).get("pageProps")) or {}
    stories = ((page_props.get("spotlightFeed") or {}).get("spotlightStories")) or []
    if stories:
        for story in stories:
            story_id = (((story or {}).get("story") or {}).get("storyId") or {}).get("value")
            if story_id == video_id:
                metadata = (story or {}).get("metadata")
                if isinstance(metadata, dict):
                    return metadata
        raise SnapchatError(f"spotlightStories 里没有视频 {video_id}")
    top = page_props.get("videoMetadata")
    if isinstance(top, dict) and top:
        return {"videoMetadata": top}
    raise SnapchatError("页面数据里没有可用的视频元数据")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any) -> int | None:
    """Snapchat 的计数是字符串，且 **-1 表示不可用**（留空，不显示）。"""
    count = to_int(value)
    return None if count is None or count < 0 else count


def _duration_seconds(value: Any) -> int:
    """``durationMs``（毫秒，字符串）→ 整秒。"""
    milliseconds = to_int(value)
    if milliseconds is None or milliseconds <= 0:
        return 0
    return round(milliseconds / 1000)


@dataclass
class SnapchatVideo:
    """一条 Spotlight 视频的解析结果。"""

    video_id: str
    url: str
    """媒体直链（明文 https）。"""
    title: str = ""
    description: str = ""
    thumbnail_url: str = ""
    duration: int = 0
    width: int = 0
    height: int = 0
    author_name: str = ""
    author_handle: str = ""
    author_url: str = ""
    published_at: datetime | None = None
    view_count: int | None = None
    hashtags: tuple[str, ...] = ()

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any], video_id: str) -> SnapchatVideo:
        video_metadata = metadata.get("videoMetadata") or {}
        content_url = _text(video_metadata.get("contentUrl"))
        if not content_url:
            raise SnapchatError("视频 metadata 里取不到 contentUrl")
        creator = (video_metadata.get("creator") or {}).get("personCreator") or {}
        view_count = video_metadata.get("viewCount")
        if view_count in (None, ""):
            view_count = (metadata.get("engagementStats") or {}).get("viewCount")
        return cls(
            video_id=video_id,
            url=content_url,
            title=_text(video_metadata.get("name")),
            # 正文取**视频级** description（真文案）；videoMetadata.description 是固定模板，不用
            description=_text(metadata.get("description")),
            thumbnail_url=_text(video_metadata.get("thumbnailUrl")),
            duration=_duration_seconds(video_metadata.get("durationMs")),
            width=to_int(video_metadata.get("width")) or 0,
            height=to_int(video_metadata.get("height")) or 0,
            # 显示名优先，没有就用用户名（渲染成 "名字 @用户名"）
            author_name=_text(creator.get("name")) or _text(creator.get("username")),
            author_handle=_text(creator.get("username")),
            author_url=_text(creator.get("url")),
            published_at=to_datetime(video_metadata.get("uploadDateMs")),
            view_count=_count(view_count),
            hashtags=tuple(tag for tag in (metadata.get("hashtags") or []) if isinstance(tag, str) and tag.strip()),
        )


def parse_spotlight(html: str, video_id: str) -> SnapchatVideo:
    """纯函数：页面 HTML + 视频 ID → :class:`SnapchatVideo`。"""
    metadata = _select_story_metadata(extract_next_data(html), video_id)
    return SnapchatVideo.from_metadata(metadata, video_id)


async def fetch_video(
    url: str,
    *,
    proxy: str | None = None,
    cookie: dict[str, str] | None = None,
) -> SnapchatVideo:
    """唯一的网络入口：请求 spotlight 页面并解析出直链与元数据。"""
    video_id = get_video_id(url)
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            html = response.text
    except http.HTTPError as e:
        raise SnapchatError(f"请求页面失败: {e}") from e
    return parse_spotlight(html, video_id)


__all__ = [
    "SnapchatError",
    "SnapchatVideo",
    "extract_next_data",
    "fetch_video",
    "get_video_id",
    "parse_spotlight",
]
