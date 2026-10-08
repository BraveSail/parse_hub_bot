"""Facebook 视频页的自研解析（不依赖 yt-dlp）。

**直链形态（实测结论）**：Facebook 视频页把**明文渐进式地址**直接内嵌在 HTML 的
``<script data-sjs>`` JSON 里（``RelayPrefetchedStreamCache`` 路由）：

    ...result.data.video.story.attachments[0].media.videoDeliveryLegacyFields
        = {
            "browser_native_sd_url": "https://video-*.fbcdn.net/.../xxx.mp4?...",
            "browser_native_hd_url": "https://video-*.fbcdn.net/.../yyy.mp4?...",
            "dash_manifest_xml_string": "<?xml ... <MPD ...",
            "dash_manifest_url": "https://www.facebook.com/dash_mpd_debug.mpd?v=<id>&dummy=.mpd",
          }

实测（2026-10-08，匿名、curl_cffi chrome150）：页面 HTTP 200 无登录墙，
``browser_native_sd_url`` 直接可下（HTTP 206 ``video/mp4``，``Accept-Ranges: bytes``）。
所以本 provider **只走渐进式直链**，不碰 MPD；拿不到渐进式地址（只有 DASH）时
明确抛错，交由上层处理（见 ``parse_video_html`` 的 "DASH" 分支）。

网络入口只有一个（``FacebookAPI.get_video``），其余全是纯函数，便于离线单测。
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..utils import http

_VIDEO_PAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

#: 内嵌 JSON 都在 ``<script ... data-sjs>{...}</script>`` 里（Relay 预取缓存）。
_SJS_BLOCK_RE = re.compile(r"data-sjs>({.*?})</script>", re.DOTALL)

#: 渐进式直链字段，按画质优先级排列。
_PROGRESSIVE_KEYS = (
    "browser_native_hd_url",
    "playable_url_quality_hd",
    "browser_native_sd_url",
    "playable_url",
)

#: og / twitter 元信息（页面兜底用）。
_META_RE_TMPL = r'<meta\s+(?:property|name)="{name}"\s+content="([^"]*)"'


class FacebookAPIError(RuntimeError):
    """Facebook 页面请求或解析失败。"""


@dataclass(slots=True)
class FacebookVideo:
    """从视频页解析出的结构化结果。"""

    video_id: str
    url: str
    """渐进式直链（真正的媒体地址，不是页面地址）。"""
    title: str = ""
    content: str = ""
    author_name: str = ""
    author_url: str = ""
    published_at: int | None = None
    """发布时间（unix 秒）。"""
    view_count: int | None = None
    duration: float | None = None
    """时长（秒）。"""
    width: int = 0
    height: int = 0
    thumb_url: str = ""


# ── 纯解析辅助 ─────────────────────────────────────────────────────────────


def _iter_dicts(obj: Any) -> Iterator[dict]:
    """深度优先遍历对象里的所有 dict（顺序确定：按 dict 的插入顺序压栈）。"""
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _json_blocks(html: str) -> list[dict]:
    blocks: list[dict] = []
    for raw in _SJS_BLOCK_RE.findall(html):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            blocks.append(parsed)
    return blocks


def _meta(html: str, name: str) -> str | None:
    match = re.search(_META_RE_TMPL.format(name=re.escape(name)), html)
    if not match:
        return None
    return html_lib.unescape(match.group(1)).strip() or None


def _as_url(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("http") else None


def _legacy_of(node: dict) -> dict:
    legacy = node.get("videoDeliveryLegacyFields")
    return legacy if isinstance(legacy, dict) else {}


def _lookup(node: dict, *keys: str) -> Any:
    """在节点自身与 ``videoDeliveryLegacyFields`` 里按顺序找第一个非空值。"""
    legacy = _legacy_of(node)
    for key in keys:
        for source in (node, legacy):
            value = source.get(key)
            if value not in (None, "", []):
                return value
    return None


def _node_id(node: dict) -> str | None:
    for key in ("videoId", "id"):
        value = _lookup(node, key)
        if value not in (None, ""):
            return str(value)
    legacy_id = _legacy_of(node).get("id")
    return str(legacy_id) if legacy_id not in (None, "") else None


def _progressive_urls(node: dict) -> dict[str, str]:
    """收集节点里的渐进式直链（含新 schema 的 ``progressive_urls``）。"""
    found: dict[str, str] = {}
    for key in _PROGRESSIVE_KEYS:
        url = _as_url(_lookup(node, key))
        if url:
            found[key] = url

    fragment = node.get("videoDeliveryResponseFragment")
    if isinstance(fragment, dict):
        result = fragment.get("videoDeliveryResponseResult")
        if isinstance(result, dict):
            for entry in result.get("progressive_urls") or []:
                if not isinstance(entry, dict):
                    continue
                url = _as_url(entry.get("progressive_url"))
                if not url:
                    continue
                quality = str(((entry.get("metadata") or {}).get("quality")) or "").lower()
                found.setdefault("browser_native_hd_url" if "hd" in quality else "browser_native_sd_url", url)
    return found


def _has_dash(node: dict) -> bool:
    for key in ("dash_manifest_xml_string", "dash_manifest_url"):
        if isinstance(_lookup(node, key), str):
            return True
    return False


def _pick_url(urls: dict[str, str]) -> str | None:
    for key in _PROGRESSIVE_KEYS:
        if url := urls.get(key):
            return url
    return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _duration_seconds(node: dict) -> float | None:
    millis = _to_float(_lookup(node, "playable_duration_in_ms"))
    if millis and millis > 0:
        return millis / 1000.0
    return _to_float(_lookup(node, "length_in_second"))


def _thumbnail(node: dict) -> str:
    for key in ("preferred_thumbnail", "thumbnailImage"):
        holder = _lookup(node, key)
        if isinstance(holder, dict):
            image = holder.get("image")
            if isinstance(image, dict):
                if url := _as_url(image.get("uri")):
                    return url
    return ""


def _subtree_contains(node: Any, needle: str) -> bool:
    """节点子树（序列化后）是否引用了目标视频 id。"""
    try:
        return needle in json.dumps(node)
    except (TypeError, ValueError):
        return False


def _id_matched(all_dicts: list[dict], vid: str) -> list[dict]:
    return [node for node in all_dicts if _node_id(node) == vid]


def _most_specific(all_dicts: list[dict], vid: str, predicate) -> list[dict]:
    """子树引用了目标 id 的节点里，挑体积最小的（越小越贴近目标，避免命中整块祖先）。"""
    candidates = [node for node in all_dicts if predicate(node) and _subtree_contains(node, vid)]
    candidates.sort(key=lambda node: len(json.dumps(node)))
    return candidates[:20]


def _extract_author(all_dicts: list[dict], vid: str) -> tuple[str, str]:
    def first_named(scope: dict) -> tuple[str, str]:
        """返回 (名字, 主页)。优先带主页的 actor；只有名字的留作兜底。"""
        name_only = ("", "")
        for node in _iter_dicts(scope):
            actors = node.get("actors")
            if isinstance(actors, list):
                for actor in actors:
                    if isinstance(actor, dict) and str(actor.get("name") or "").strip():
                        name = str(actor["name"]).strip()
                        url = _as_url(actor.get("url")) or ""
                        if url:
                            return name, url
                        if not name_only[0]:
                            name_only = (name, url)
            owner = node.get("video_owner")
            if isinstance(owner, dict) and str(owner.get("name") or "").strip():
                name = str(owner["name"]).strip()
                url = _as_url(owner.get("url")) or ""
                if url:
                    return name, url
                if not name_only[0]:
                    name_only = (name, url)
        return name_only

    name_only = ("", "")
    for scope in (
        *_id_matched(all_dicts, vid),
        *_most_specific(all_dicts, vid, lambda n: "actors" in n or "video_owner" in n),
    ):
        name, url = first_named(scope)
        if name and url:
            return name, url
        if name and not name_only[0]:
            name_only = (name, url)
    return name_only


def _extract_caption(all_dicts: list[dict], vid: str, chosen: dict) -> str:
    def first_text(scope: dict) -> str:
        for node in _iter_dicts(scope):
            message = node.get("message")
            if isinstance(message, dict):
                text = message.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    for scope in (*_id_matched(all_dicts, vid), chosen):
        if caption := first_text(scope):
            return caption
    for scope in _most_specific(all_dicts, vid, lambda n: isinstance(n.get("message"), dict)):
        if caption := first_text(scope):
            return caption
    return ""


def _extract_view_count(all_dicts: list[dict], vid: str, chosen: dict) -> int | None:
    def first_count(scope: dict) -> int | None:
        for node in _iter_dicts(scope):
            count = _to_int(node.get("video_view_count"))
            if count is not None:
                return count
        return None

    for scope in (*_id_matched(all_dicts, vid), chosen):
        if (count := first_count(scope)) is not None:
            return count
    for scope in _most_specific(all_dicts, vid, lambda n: "video_view_count" in n):
        if (count := first_count(scope)) is not None:
            return count
    return None


def _video_id_from_url(url: str) -> str | None:
    query = parse_qs(urlparse(url).query)
    if ids := query.get("v"):
        return ids[0]
    for pattern in (r"/videos/(\d+)", r"/reel/(\d+)", r"[?&]v=(\d+)", r"/videos/(pfbid[0-9A-Za-z]+)"):
        if match := re.search(pattern, url):
            return match.group(1)
    return None


def _clean_title(raw_title: str | None, fallback: str) -> str:
    """把 og:title 里的「``N views · M reactions | ``」前缀去掉，留下干净标题。"""
    title = (raw_title or "").strip()
    if " | " in title:
        prefix, rest = title.split(" | ", 1)
        if re.search(r"\bviews?\b|\breactions?\b", prefix, re.IGNORECASE):
            title = rest.strip()
    return title or fallback


# ── 主解析（纯函数：吃 HTML → 结构化结果）─────────────────────────────────


def parse_video_html(html: str, video_id: str | None = None) -> FacebookVideo:
    """从视频页 HTML 里解析出直链与元数据。

    :param video_id: 目标视频 id（从 URL 得到）。给了就按 id 精确挑选（视频页里
        还嵌着"相关视频"，不按 id 挑会拿错）；拿不到时退化为"信息最全的那个"。
    :raises FacebookAPIError: 页面里找不到任何视频数据（登录墙 / 视频已删等）。
    """
    blocks = _json_blocks(html)
    all_dicts = [node for block in blocks for node in _iter_dicts(block)]

    # ① 收集带直链的媒体节点（同一视频可能出现多份，合并取信息最全的）。
    best_by_id: dict[str, tuple[tuple, dict]] = {}
    unkeyed: list[tuple[tuple, dict]] = []
    for node in all_dicts:
        urls = _progressive_urls(node)
        if not urls:
            continue
        score = (len(urls), bool(_lookup(node, "width")), bool(_duration_seconds(node)), bool(_thumbnail(node)))
        vid = _node_id(node)
        if vid is None:
            unkeyed.append((score, node))
        elif vid not in best_by_id or score > best_by_id[vid][0]:
            best_by_id[vid] = (score, node)

    node: dict | None = None
    if video_id:
        # 按 id 精确挑选：视频页里还嵌着"相关视频"，按 id 才不会被带偏；
        # URL 里的 id 在页面里找不到时必须报错，不能静默拿别的视频。
        node = best_by_id[video_id][1] if video_id in best_by_id else None
    elif best_by_id:
        node = max(best_by_id.values(), key=lambda c: c[0])[1]
    elif unkeyed:
        node = max(unkeyed, key=lambda c: c[0])[1]

    if node is None:
        raise FacebookAPIError("页面里未找到该视频的播放地址（可能已删除 / 仅 DASH / 需要登录）")

    urls = _progressive_urls(node)
    url = _pick_url(urls)
    if not url:
        if _has_dash(node):
            raise FacebookAPIError("仅找到 DASH manifest，未实现 MPD 分片下载")
        raise FacebookAPIError("未找到可下载的直链")

    vid = _node_id(node) or (video_id or "")

    # ② 元数据：优先在"id 等于目标视频"的节点子树里找（同页还嵌着相关视频，
    #    不限定范围会取到别的视频的作者/文案）；找不到再退到"子树引用了该 id"
    #    的最小节点（post 页的作者挂在更外层的故事节点上）。
    author_name, author_url = _extract_author(all_dicts, vid)
    caption = _extract_caption(all_dicts, vid, node)
    view_count = _extract_view_count(all_dicts, vid, node)
    if not caption:
        caption = _meta(html, "og:description") or ""
    title = _clean_title(_meta(html, "og:title"), caption or f"Facebook video #{vid}")

    return FacebookVideo(
        video_id=vid,
        url=url,
        title=title,
        content=caption,
        author_name=author_name,
        author_url=author_url,
        published_at=_to_int(_lookup(node, "publish_time", "creation_time")),
        view_count=view_count,
        duration=_duration_seconds(node),
        width=_to_int(_lookup(node, "width")) or 0,
        height=_to_int(_lookup(node, "height")) or 0,
        thumb_url=_thumbnail(node),
    )


# ── 唯一网络入口 ───────────────────────────────────────────────────────────


class FacebookAPI:
    """Facebook 视频页解析入口（匿名，无需 cookie）。"""

    def __init__(self, *, proxy: str | None = None, timeout: float = 30.0):
        self.proxy = proxy
        self.timeout = timeout

    async def get_video(self, url: str) -> FacebookVideo:
        """抓取视频页并解析。``/share/v/<token>`` 这类短链靠跟随重定向拿到 id。"""
        try:
            async with http.AsyncClient(
                proxy=self.proxy,
                timeout=self.timeout,
                headers={"User-Agent": _VIDEO_PAGE_USER_AGENT},
            ) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
        except http.HTTPError as exc:
            raise FacebookAPIError(f"请求 Facebook 页面失败: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一转成自己的错误
            raise FacebookAPIError(f"请求 Facebook 页面出错: {exc}") from exc

        html = response.text
        if not isinstance(html, str) or not html:
            raise FacebookAPIError("Facebook 页面响应为空")

        final_url = str(getattr(response, "url", url))
        video_id = _video_id_from_url(final_url) or _video_id_from_url(url)
        return parse_video_html(html, video_id)


__all__ = [
    "FacebookAPI",
    "FacebookAPIError",
    "FacebookVideo",
    "parse_video_html",
]
