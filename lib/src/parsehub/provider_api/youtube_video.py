"""YouTube 视频 / 音乐解析 —— **自研, 不走 yt-dlp**。

背景
----
YouTube 的反爬本质是 **IP 判定**: 同一条 ``/youtubei/v1/player`` 请求, 从被标记的
出口发出去会被 ``LOGIN_REQUIRED`` / ``UNPLAYABLE`` 挡掉, 换一个干净出口就正常。
一旦出口没问题, 只要用一个**返回明文 ``url`` 的 innertube client** 请求 player API,
``streamingData.formats`` / ``adaptiveFormats`` 里每一项都带**可直接下载的明文直链**
(``https://…googlevideo.com/videoplayback?…``) —— 不需要 signature / nsig 解密,
也不需要 JS 解释器 (那是网页版 client 才要的东西)。

**client 的选择判据是"既要给明文 url, 又不要 PO token"** —— 两条都要满足, 缺一条就废:

| client | 带明文 url 的流 | 直链实测 |
| --- | --- | --- |
| ``ANDROID`` | 30 条里**只有 1 条** (itag=18, 360p) | ✅ 206 |
| ``ANDROID_VR`` | 27 条全部 | ❌ 403 (`GVS_PO_TOKEN_POLICY: required=True`) |
| ``VISIONOS`` | 27 条全部 (含 1080p avc1 / 2160p) | ✅ 206 / 830KB/s |

所以只用 ``VISIONOS`` (见 ``CLIENTS`` 的说明, 别把 ``ANDROID_VR`` 加回来当兜底)。
**选流策略**见 ``select_streams``: 默认取 ≤1080p 的最高画质 (同分辨率优先 H.264, 便于
mux 成 mp4), 音视频分离时由解析器层用 ffmpeg ``-c copy`` 合并
(见 ``parsers/parser/youtube.py``)。

本模块是"播放器逻辑"的归口: 纯函数 (吃 player JSON) + 唯一网络入口 (走 ``utils.http``)。
**不要**把它塞进 ``provider_api/youtube.py`` (那里是帖子/封面, 与播放器无关)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..utils import http
from ..utils.helpers import to_int

# ── innertube client 常量 ──────────────────────────────────────────────────
#
# **这些版本号会过期**: YouTube 会周期性收紧老 client (要求更高 clientVersion、
# 换 deviceModel, 或干脆停止返回明文 url)。一旦解析集体失败, 第一件事就是照上游
# yt-dlp 的 ``INNERTUBE_CLIENTS`` 更新这里的字段:
#   https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_base.py
# 常量集中在这一处, 更新时只改这里 —— 不要把版本号散落到函数体里。
#
# ``_user_agent`` / ``_x_client_name`` 是带下划线的**私有**键, 不会进 innertube
# ``context.client`` (见 ``_client_context``)。``X-Youtube-Client-Name`` 的数值来自
# 上游 ``INNERTUBE_CLIENTS`` 的 client_id。

VISIONOS: dict[str, Any] = {
    "clientName": "VISIONOS",
    "clientVersion": "1.02",
    "deviceMake": "Apple",
    "deviceModel": "RealityDevice17,1",
    "osName": "visionOS",
    "osVersion": "26.5.23O471",
    "hl": "en",
    "gl": "US",
    "_user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.0 Safari/605.1.15"
    ),
    "_x_client_name": "101",
}

#: 按尝试顺序排列 (首选在前)。``fetch_video`` 依次尝试, 全失败才抛错。
#:
#: **只留 VISIONOS**（2026-10-08 实测定案）:
#:
#: | client | 带明文 url 的流 | 直链能否下载 |
#: | --- | --- | --- |
#: | ``ANDROID`` | 30 条里**只有 1 条**（itag=18, 640x360）| ✅ 206 |
#: | ``ANDROID_VR`` | 27 条全部 | ❌ **403** |
#: | ``VISIONOS`` | 27 条全部（含 1080p avc1 / 2160p）| ✅ 206 |
#:
#: - ``ANDROID`` 能下但**只有 360p** —— 其余 29 条既没有 ``url`` 也没有 ``cipher`` 字段,
#:   自研路径根本拿不到, 想要高清只能靠别的 client。
#: - ``ANDROID_VR`` 的 url 看着齐, 实际**必然 403**: 上游 ``INNERTUBE_CLIENTS`` 里
#:   ``android_vr`` 的 ``GVS_PO_TOKEN_POLICY`` 是 ``required=True``（要 PO token）,
#:   而 ``visionos`` 是 ``required=False``。**别再加回它当兜底** —— 它只会掩盖真问题。
#: - 所以这一类 client 的选择判据是: **既要给明文 url, 又不要 PO token**。两者都满足的
#:   目前只有 ``VISIONOS``。
CLIENTS: tuple[dict[str, Any], ...] = (VISIONOS,)

PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"
"""innertube player 接口 (两种 client 共用)。"""

DEFAULT_MAX_HEIGHT = 1080
"""默认画质上限。

1080p 是 H.264 (``avc1``) 在 YouTube 上的**最高档** —— 再往上只有 AV1 / VP9,
体积大、mux 进 mp4 的通用性也差, 而在 Telegram 里 1080p 已经够看。想要 4K 就把
``select_streams(max_height=0)`` 放开 (0 = 不设上限)。
"""


class YoutubeVideoError(RuntimeError):
    """player API 请求失败, 或响应里没有可用的流 (playabilityStatus 非 OK)。"""


# ── 数据结构 ──────────────────────────────────────────────────────────────

_VIDEO_CODECS = ("avc1", "avc3", "av01", "vp9", "vp8", "hev1", "hvc1", "dvh1")
_AUDIO_CODECS = ("mp4a", "opus", "vorbis", "aac", "ec-3", "ac-3")
_CODECS_RE = re.compile(r'codecs="([^"]+)"')


@dataclass(frozen=True, slots=True)
class Stream:
    """player 响应里的一条流 (已确认带明文 ``url``)。"""

    itag: int
    url: str
    mime_type: str
    codec: str = ""
    bitrate: int = 0
    width: int = 0
    height: int = 0
    fps: int = 0
    content_length: int = 0
    quality_label: str = ""
    has_video: bool = False
    has_audio: bool = False

    @property
    def is_muxed(self) -> bool:
        """音视频合一 (``formats`` 里的 itag=18 那种) —— 单文件即可下载。"""
        return self.has_video and self.has_audio


@dataclass(slots=True)
class YoutubeVideo:
    """一条视频的解析结果 (元数据 + 全部可用流)。"""

    video_id: str
    title: str = ""
    description: str = ""
    author_name: str = ""
    channel_id: str = ""
    thumbnail: str = ""
    duration: int = 0
    view_count: int | None = None
    user_agent: str = ""
    """请求 player 所用 client 的 User-Agent —— 下载直链时一并带上。"""
    streams: list[Stream] = field(default_factory=list)

    @property
    def author_url(self) -> str:
        """作者主页。

        只有 ``videoDetails.channelId`` 可用 —— 拼 ``/channel/<id>`` 是**平台自己
        的规范地址** (一定能打开), 而 handle 不在 player 响应里, 拼不了
        (``/@{channel_id}`` 打不开, 上游 yt-dlp 也是这么说的)。
        """
        return f"https://www.youtube.com/channel/{self.channel_id}" if self.channel_id else ""


@dataclass(frozen=True, slots=True)
class SelectedStreams:
    """选流结果。

    ``audio_url`` 为 ``None`` 表示单文件 (音视频合一) —— 用项目下载器直接下次即可;
    非 ``None`` 表示音视频分离, 需要分别下载后用 ffmpeg 合并。
    """

    video_url: str
    audio_url: str | None
    width: int
    height: int
    duration: int
    ext: str = "mp4"


# ── 纯函数: player JSON -> 结构化 ─────────────────────────────────────────

def video_id_from_url(url: str) -> str:
    """从链接里取 11 位视频 ID；取不到返回空串。"""
    from .youtube import VIDEO_ID_RE  # 复用既有正则, 不重复维护

    match = VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _stream_codecs(mime_type: str) -> list[str]:
    """``video/mp4; codecs="avc1.640028, mp4a.40.2"`` -> ``["avc1", "mp4a"]``。"""
    codecs: list[str] = []
    for block in _CODECS_RE.findall(mime_type):
        for token in block.split(","):
            name = token.strip().split(".")[0].lower()
            if name:
                codecs.append(name)
    return codecs


def _stream_from_raw(raw: Any) -> Stream | None:
    """一条 ``formats`` / ``adaptiveFormats`` 条目 -> ``Stream``；没有明文 url 则丢弃。"""
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not url:
        return None
    mime_type = str(raw.get("mimeType") or "")
    codecs = _stream_codecs(mime_type)
    has_video = mime_type.startswith("video/") or any(codec in _VIDEO_CODECS for codec in codecs)
    has_audio = mime_type.startswith("audio/") or any(codec in _AUDIO_CODECS for codec in codecs)
    primary = next(
        (codec for codec in codecs if codec in _VIDEO_CODECS),
        next((codec for codec in codecs if codec in _AUDIO_CODECS), ""),
    )
    return Stream(
        itag=to_int(raw.get("itag")) or 0,
        url=str(url),
        mime_type=mime_type,
        codec=primary,
        bitrate=to_int(raw.get("bitrate")) or 0,
        width=to_int(raw.get("width")) or 0,
        height=to_int(raw.get("height")) or 0,
        fps=to_int(raw.get("fps")) or 0,
        content_length=to_int(raw.get("contentLength")) or 0,
        quality_label=str(raw.get("qualityLabel") or ""),
        has_video=has_video,
        has_audio=has_audio,
    )


def _playability_error(data: dict[str, Any]) -> str | None:
    """``playabilityStatus`` 非 OK 时返回可读原因；OK 返回 ``None``。"""
    status = data.get("playabilityStatus") or {}
    code = str(status.get("status") or "")
    if code in ("", "OK"):
        return None
    detail = status.get("reason") or (status.get("errorScreen") or {}).get("playerErrorMessageRenderer", {}).get(
        "reason", {}
    )
    if isinstance(detail, dict):
        detail = detail.get("simpleText") or "".join(run.get("text", "") for run in detail.get("runs", []))
    return f"{code}{(': ' + str(detail)) if detail else ''}"


def parse_player_response(data: dict[str, Any], *, video_id: str = "") -> YoutubeVideo:
    """把 innertube player 响应转成 ``YoutubeVideo``（纯函数, 便于离线测试）。

    :raises YoutubeVideoError: playabilityStatus 非 OK, 或缺 ``videoDetails`` /
        没有任何带明文 url 的流 —— **不静默返回空结果**。
    """
    if error := _playability_error(data):
        raise YoutubeVideoError(error)

    details = data.get("videoDetails")
    if not isinstance(details, dict):
        raise YoutubeVideoError("player 响应缺少 videoDetails")

    streaming = data.get("streamingData") or {}
    raw_streams = list(streaming.get("formats") or []) + list(streaming.get("adaptiveFormats") or [])
    streams = [stream for raw in raw_streams if (stream := _stream_from_raw(raw)) is not None]
    if not streams:
        raise YoutubeVideoError("player 响应里没有带明文 url 的流")

    thumbnails = (details.get("thumbnail") or {}).get("thumbnails") or []
    thumbnail = ""
    if thumbnails:
        biggest = max(thumbnails, key=lambda t: int(t.get("width") or 0))
        thumbnail = str(biggest.get("url") or "")

    return YoutubeVideo(
        video_id=str(details.get("videoId") or video_id),
        title=str(details.get("title") or "").strip(),
        description=str(details.get("shortDescription") or "").strip(),
        author_name=str(details.get("author") or "").strip(),
        channel_id=str(details.get("channelId") or "").strip(),
        thumbnail=thumbnail,
        duration=to_int(details.get("lengthSeconds")) or 0,
        view_count=to_int(details.get("viewCount")),
        streams=streams,
    )


# ── 纯函数: 选流 ──────────────────────────────────────────────────────────

#: 同分辨率下优先 H.264 (avc1) —— mux 成 mp4 最通用; AV1/VP9 次之。
_VIDEO_RANK = {"avc1": 0, "avc3": 0, "av01": 1, "vp9": 2, "vp8": 3}
_VIDEO_RANK_DEFAULT = 9
#: 音频优先 AAC (mp4a), 与 mp4 容器最搭; opus 次之。
_AUDIO_RANK = {"mp4a": 0, "opus": 1}
_AUDIO_RANK_DEFAULT = 9


def _video_sort_key(stream: Stream) -> tuple:
    # 分辨率优先, 再看编码 (小值优先), 然后帧率 / 码率
    return (
        -stream.height,
        _VIDEO_RANK.get(stream.codec, _VIDEO_RANK_DEFAULT),
        -stream.fps,
        -stream.bitrate,
    )


def _audio_sort_key(stream: Stream) -> tuple:
    return (_AUDIO_RANK.get(stream.codec, _AUDIO_RANK_DEFAULT), -stream.bitrate)


def _ext_of(mime_type: str) -> str:
    if "webm" in mime_type:
        return "webm"
    return "mp4"


def select_streams(
    video: YoutubeVideo,
    *,
    max_height: int = DEFAULT_MAX_HEIGHT,
    allow_mux: bool = True,
) -> SelectedStreams:
    """挑一条可下载的流（纯函数）。

    策略:

    1. ``allow_mux`` 且存在分离的视频流与音频流 -> **最高画质**: ``≤max_height`` 里
       最高的一条 (同分辨率优先 H.264) + 最优音频 (优先 AAC), 交给上层 mux。
    2. 否则退回**音视频合一**的单文件流 (最高档)。
    3. 都没有 -> 抛 ``YoutubeVideoError``（不静默出无声/空结果）。

    :param max_height: 画质上限, ``0`` 表示不设上限。
    :param allow_mux: 环境能否 mux (有 ffmpeg 才为 True)。False 时只用合一单文件,
        若又没有合一档则明确报错 —— 绝不输出无声视频。
    """
    video_streams = [s for s in video.streams if s.has_video and not s.has_audio and s.height > 0]
    audio_streams = [s for s in video.streams if s.has_audio and not s.has_video]
    muxed_streams = [s for s in video.streams if s.is_muxed]

    if max_height > 0:
        capped = [s for s in video_streams if s.height <= max_height]
        if capped:
            video_streams = capped

    if allow_mux and video_streams and audio_streams:
        best_video = min(video_streams, key=_video_sort_key)
        best_audio = min(audio_streams, key=_audio_sort_key)
        return SelectedStreams(
            video_url=best_video.url,
            audio_url=best_audio.url,
            width=best_video.width,
            height=best_video.height,
            duration=video.duration,
            ext=_ext_of(best_video.mime_type),
        )

    if muxed_streams:
        best = min(muxed_streams, key=_video_sort_key)
        return SelectedStreams(
            video_url=best.url,
            audio_url=None,
            width=best.width,
            height=best.height,
            duration=video.duration,
            ext=_ext_of(best.mime_type),
        )

    if video_streams and audio_streams:
        raise YoutubeVideoError("需要 ffmpeg 合并音视频, 但当前环境没有 ffmpeg")
    raise YoutubeVideoError("响应里没有可下载的流")


# ── 唯一网络入口 ──────────────────────────────────────────────────────────

def _client_context(spec: dict[str, Any]) -> dict[str, Any]:
    """去掉私有键 (``_`` 开头), 只留 innertube ``context.client`` 认得的字段。"""
    return {key: value for key, value in spec.items() if not key.startswith("_")}


async def _request_player(
    video_id: str,
    spec: dict[str, Any],
    *,
    proxy: str | None,
    cookie: dict[str, str] | None,
) -> dict[str, Any]:
    body = {
        "context": {"client": _client_context(spec)},
        "videoId": video_id,
        "contentCheckOk": True,
        "racyCheckOk": True,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": spec["_user_agent"],
        "X-Youtube-Client-Name": spec["_x_client_name"],
        "X-Youtube-Client-Version": spec["clientVersion"],
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
        response = await client.post(PLAYER_URL, data=json.dumps(body).encode(), headers=headers)
    if response.status_code != 200:
        raise YoutubeVideoError(f"player API 返回 HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise YoutubeVideoError("player API 返回的不是 JSON") from exc


async def fetch_video(
    video_id: str,
    *,
    proxy: str | None = None,
    cookie: dict[str, str] | None = None,
) -> YoutubeVideo:
    """请求 player API 并解析。

    请求 player API 并解析（``CLIENTS`` 当前只有 ``VISIONOS``；全部失败抛 ``YoutubeVideoError``）。
    """
    if not video_id:
        raise YoutubeVideoError("缺少视频 ID")

    errors: list[str] = []
    for spec in CLIENTS:
        try:
            data = await _request_player(video_id, spec, proxy=proxy, cookie=cookie)
            video = parse_player_response(data, video_id=video_id)
        except YoutubeVideoError as exc:
            errors.append(f"{spec['clientName']}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常都要并入原因列表
            errors.append(f"{spec['clientName']}: {exc}")
        else:
            video.user_agent = str(spec["_user_agent"])
            return video

    raise YoutubeVideoError("player API 解析失败: " + "; ".join(errors))


__all__ = [
    "CLIENTS",
    "DEFAULT_MAX_HEIGHT",
    "PLAYER_URL",
    "VISIONOS",
    "SelectedStreams",
    "Stream",
    "YoutubeVideo",
    "YoutubeVideoError",
    "fetch_video",
    "parse_player_response",
    "select_streams",
    "video_id_from_url",
]
