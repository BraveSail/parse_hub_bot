"""YouTube 频道/视频的标题与封面。

**不是解析 YouTube 视频本身**（那是 ``parsers/parser/youtube.py`` + yt-dlp 的活），
而是给**别处正文里提到的 YouTube 链接**配一张封面（用户要求：正文引用的 YouTube
链接要像 bilibili 引用的视频那样，有封面、标题可点）。

两条路：

- **视频**（``watch?v=`` / ``youtu.be/`` / ``shorts/``）走 oembed，返回 JSON，
  ``thumbnail_url`` 就是视频缩略图 —— 又快又稳。
- **频道**（``/@handle`` 等）没有 oembed，只能抓频道页的 ``og:title`` / ``og:image``
  （拿到的是频道头像/主视觉，频道本来就没有"视频缩略图"）。

封面抓不到**不该影响解析**：任何异常都返回 ``None``，调用方跳过即可。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..utils import http

#: 视频 ID: watch?v= / youtu.be/ / embed/ / shorts/ / live/
VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|embed/|shorts/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)

#: 频道页 (拿不到视频 ID 时按频道处理)
CHANNEL_RE = re.compile(
    r"youtube\.com/(?:@[A-Za-z0-9_.\-]+|channel/[A-Za-z0-9_-]+|c/[A-Za-z0-9_.\-]+|user/[A-Za-z0-9_.\-]+)"
)

#: 正文里出现的 YouTube 链接 (含裸域名与 http/https)
_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s<>\"']+|@[A-Za-z0-9_.\-]+"
    r"|channel/[A-Za-z0-9_-]+|c/[A-Za-z0-9_.\-]+|user/[A-Za-z0-9_.\-]+|shorts/[A-Za-z0-9_-]+"
    r"|live/[A-Za-z0-9_-]+|embed/[A-Za-z0-9_-]+)"
    r"|youtu\.be/[A-Za-z0-9_-]+)"
)

_OEMBED = "https://www.youtube.com/oembed?format=json&url={url}"

_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.I)
_OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]*)"', re.I)




@dataclass
class YoutubeCard:
    """一个 YouTube 链接的卡片信息。"""

    url: str
    title: str
    cover_url: str


def find_youtube_links(text: str | None) -> list[str]:
    """正文里出现的 YouTube 链接 (去重, 保持出现顺序)。"""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for link in _LINK_RE.findall(text):
        link = link.rstrip("。，、）)】].,")
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def _trim_cover(url: str) -> str:
    """去掉尺寸后缀取原图。

    ``…=s900-c-k-c0x00ffffff-no-rj`` 这类尾巴是 YouTube 的尺寸修饰, 截掉 ``=`` 后
    的部分拿到原图 —— 让下载器按自己要的尺寸处理, 而不是被 s900 钉死。
    """
    return url.split("=", 1)[0] if "=" in url else url


async def _fetch_page(url: str, *, proxy=None) -> str:
    """抓频道页。

    频道页约 1.1MB, 但 **og 标签不在开头** —— 实测它落在 400KB 之后 (前面是内联
    脚本与数据), 所以不能"只读前 N KB"就去找, 必须拿完整文本。
    """
    async with http.AsyncClient(proxy=proxy, follow_redirects=True) as cli:
        resp = await cli.get(url)
        if resp.status_code != 200:
            return ""
        return resp.text or ""


async def _fetch_video(video_url: str, *, proxy=None) -> YoutubeCard | None:
    async with http.AsyncClient(proxy=proxy, follow_redirects=True) as cli:
        resp = await cli.get(_OEMBED.format(url=video_url))
        if resp.status_code != 200:
            return None
        data = json.loads(resp.text)
    title = (data.get("title") or "").strip()
    cover = _trim_cover((data.get("thumbnail_url") or "").strip())
    if not title or not cover:
        return None
    return YoutubeCard(url=video_url, title=title, cover_url=cover)


async def _fetch_channel(channel_url: str, *, proxy=None) -> YoutubeCard | None:
    html = await _fetch_page(channel_url, proxy=proxy)
    if not html:
        return None
    title_m = _OG_TITLE_RE.search(html)
    image_m = _OG_IMAGE_RE.search(html)
    if not (title_m and image_m):
        return None
    title = title_m.group(1).strip()
    cover = _trim_cover(image_m.group(1).strip())
    if not title or not cover:
        return None
    return YoutubeCard(url=channel_url, title=title, cover_url=cover)


async def fetch_card(url: str, *, proxy=None) -> YoutubeCard | None:
    """取一个 YouTube 链接的标题与封面。

    视频优先走 oembed; 频道 (或 oembed 失败) 退回抓页面。**失败一律返回 None** ——
    封面是锦上添花, 不该让整条解析失败。
    """
    if not url:
        return None
    try:
        if VIDEO_ID_RE.search(url):
            if card := await _fetch_video(url, proxy=proxy):
                return card
        if CHANNEL_RE.search(url) or VIDEO_ID_RE.search(url):
            return await _fetch_channel(url, proxy=proxy)
    except Exception:  # noqa: BLE001 - 网络/解析异常都不该冒泡到解析流程
        return None
    return None


__all__ = ["YoutubeCard", "fetch_card", "find_youtube_links"]
