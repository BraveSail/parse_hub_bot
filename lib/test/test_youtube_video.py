"""YouTube 视频 / 音乐：自研 player API 解析与选流（不再走 yt-dlp）。

fixture:

- ``youtube_player_visionos.json`` —— 真实 ``/youtubei/v1/player`` 响应（**生产用的
  VISIONOS client**, videoId ``dQw4w9WgXcQ``）：27 条流全部带明文 url, 含 2160p/1440p/1080p
  （**全是分离流, 没有音视频合一档**）。
- ``youtube_player_android.json`` —— 真实响应（``ANDROID`` client, 同视频）：
  30 条里**只有 1 条**带明文 url（itag=18, 360p **合一档**）, 另留 1 条无 url 的
  adaptiveFormat 钉住"没有明文 url 的流会被丢弃"。合一是降级路径（环境没有 ffmpeg 时）。
- ``youtube_player_unavailable.json`` —— 非 ``OK`` 的 playability 响应（构造）。

覆盖: 明文直链提取 / 选流策略 / 元数据映射 / 非 OK 报错 / parser 产出 googlevideo 直链 /
分离流的 mux 下载接线 / 合一单文件回退 / 帖子路径回归。
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from _fakes import FakeResponse

from parsehub.parsers.parser.youtube import YtbParse, YtbVideoParseResult
from parsehub.provider_api.youtube import parse_post_page
from parsehub.provider_api.youtube_video import (
    DEFAULT_MAX_HEIGHT,
    YoutubeVideoError,
    fetch_video,
    parse_player_response,
    select_streams,
    video_id_from_url,
)
from parsehub.types import MultimediaParseResult, ParseError, VideoParseResult, VideoRef

FIXTURES = Path(__file__).parent / "fixtures"
GOOGLEVIDEO_HOST = "googlevideo.com"
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _player(name: str = "youtube_player_visionos.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _by_itag(video) -> dict:
    return {stream.itag: stream for stream in video.streams}


# ── ① 明文直链提取 + 选流策略 ──────────────────────────────────────────────


def test_player_response_keeps_only_plain_url_streams():
    """每条流都带可直接下载的明文 url（不需要 signature/nsig 解密）。"""
    video = parse_player_response(_player())
    assert video.streams
    assert all(GOOGLEVIDEO_HOST in stream.url for stream in video.streams)
    assert all(stream.url.startswith("https://") for stream in video.streams)


def test_selection_prefers_highest_quality_capped_at_1080p_with_h264():
    """默认 ≤1080p 的最高画质, 同分辨率优先 H.264（便于 mux 成 mp4）。

    fixture 里 1080p 有 avc1(137) / av01(399) / vp9(248) 三档 —— 要挑 avc1 137;
    音频 mp4a(140) 与 opus(251) 里要挑 mp4a 140。
    """
    video = parse_player_response(_player())
    by_itag = _by_itag(video)
    selected = select_streams(video, max_height=DEFAULT_MAX_HEIGHT, allow_mux=True)

    assert selected.audio_url is not None, "1080p 是分离流, 应走 mux 路径"
    assert selected.height == 1080
    assert selected.video_url == by_itag[137].url  # avc1, 不是 an 399/248
    assert selected.audio_url == by_itag[140].url  # mp4a, 不是 opus 251
    assert selected.ext == "mp4"


def test_selection_without_a_cap_can_reach_4k():
    """放开上限（max_height=0）时选到 2160p —— 证明高画质确实拿得到。"""
    video = parse_player_response(_player())
    by_itag = _by_itag(video)
    selected = select_streams(video, max_height=0, allow_mux=True)
    assert selected.height == 2160
    assert selected.video_url == by_itag[313].url


def test_selection_without_mux_falls_back_to_the_muxed_stream():
    """没有 ffmpeg 时退回合一的 360p 单文件（audio_url=None）, 不输出无声视频。

    用 ``ANDROID`` 的真实响应：它正好只给一条合一档, 代表降级场景。
    """
    video = parse_player_response(_player("youtube_player_android.json"))
    by_itag = _by_itag(video)
    selected = select_streams(video, allow_mux=False)
    assert selected.audio_url is None
    assert selected.video_url == by_itag[18].url
    assert selected.height == 360


def test_selection_raises_when_only_split_streams_and_no_mux():
    """既不能 mux、又没有合一档 -> 明确报错, 绝不静默出无声视频。"""
    video = parse_player_response(_player())
    split_only = [s for s in video.streams if not s.is_muxed]
    video.streams = split_only
    with pytest.raises(YoutubeVideoError):
        select_streams(video, allow_mux=False)


# ── ② 元数据映射 ──────────────────────────────────────────────────────────


def test_metadata_is_mapped_from_video_details():
    video = parse_player_response(_player())
    assert video.video_id == "dQw4w9WgXcQ"
    assert video.title.startswith("Rick Astley")
    assert video.author_name == "Rick Astley"
    assert video.channel_id == "UCuAXFkgsw1L7xaCfnd5JJOw"
    assert video.duration == 213
    assert video.view_count == int(_player()["videoDetails"]["viewCount"])  # 实时变化, 别写死
    assert video.description.startswith("The official video")
    assert video.thumbnail.startswith("https://i.ytimg.com/")
    assert video.author_url == "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw"


def test_missing_video_details_raises():
    with pytest.raises(YoutubeVideoError):
        parse_player_response({"playabilityStatus": {"status": "OK"}})


# ── ③ 非 OK 时报错 ────────────────────────────────────────────────────────


def test_unplayable_response_raises():
    with pytest.raises(YoutubeVideoError) as exc:
        parse_player_response(_player("youtube_player_unavailable.json"))
    assert "UNPLAYABLE" in str(exc.value)


def test_ok_response_without_any_stream_raises():
    data = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"videoId": "x", "title": "t"},
        "streamingData": {"formats": [{"itag": 18}], "adaptiveFormats": []},  # 无 url
    }
    with pytest.raises(YoutubeVideoError):
        parse_player_response(data)


# ── ④ parser 产出的 VideoRef.url 是 googlevideo 直链 ──────────────────────


def test_video_id_from_url():
    assert video_id_from_url(VIDEO_URL) == "dQw4w9WgXcQ"
    assert video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id_from_url("https://www.youtube.com/shorts/hnSeq_P3jAo") == "hnSeq_P3jAo"
    assert video_id_from_url("https://www.youtube.com/post/abc") == ""


def _parse_video_with(player: dict):
    async def fake_fetch(video_id, **_kwargs):
        return parse_player_response(player, video_id=video_id)

    with (
        patch("parsehub.parsers.parser.youtube.fetch_video", new=fake_fetch),
        patch.object(YtbParse, "_ffmpeg_available", staticmethod(lambda: True)),
    ):
        return asyncio.run(YtbParse().parse(VIDEO_URL))


def test_parser_video_ref_url_is_a_googlevideo_direct_link():
    result = _parse_video_with(_player())
    assert isinstance(result, VideoParseResult)
    assert isinstance(result.media, VideoRef)
    assert GOOGLEVIDEO_HOST in result.media.url
    assert "youtube.com/watch" not in result.media.url
    assert result.media.width == 1920 and result.media.height == 1080
    assert result.media.duration == 213
    # 元数据也带上了
    assert result.title.startswith("Rick Astley")
    assert result.author_name == "Rick Astley"
    assert result.author_url.startswith("https://www.youtube.com/channel/")
    assert result.view_count == int(_player()["videoDetails"]["viewCount"])
    assert result.content.startswith("The official video")


def test_parser_falls_back_to_muxed_when_ffmpeg_is_missing():
    async def fake_fetch(video_id, **_kwargs):
        return parse_player_response(_player("youtube_player_android.json"), video_id=video_id)

    with (
        patch("parsehub.parsers.parser.youtube.fetch_video", new=fake_fetch),
        patch.object(YtbParse, "_ffmpeg_available", staticmethod(lambda: False)),
    ):
        result = asyncio.run(YtbParse().parse(VIDEO_URL))
    assert result.media.height == 360  # itag 18 合一档


def test_parser_raises_parse_error_when_player_is_unplayable():
    async def fake_fetch(video_id, **_kwargs):
        return parse_player_response(_player("youtube_player_unavailable.json"), video_id=video_id)

    with patch("parsehub.parsers.parser.youtube.fetch_video", new=fake_fetch):
        with pytest.raises(ParseError):
            asyncio.run(YtbParse().parse(VIDEO_URL))


def test_parser_rejects_a_url_without_a_video_id():
    with pytest.raises(ParseError):
        asyncio.run(YtbParse().parse("https://www.youtube.com/watch"))
    assert YtbParse.match("https://www.youtube.com/watch")


# ── fetch_video: 网络入口 ─────────────────────────────────────────────────


def test_fetch_video_uses_the_visionos_client():
    """只请求 VISIONOS（#101）—— 它是唯一"给明文 url 且不要 PO token"的 client。

    ``ANDROID_VR``（#28）虽然也给全部明文 url, 但直链实测 403（要 PO token）,
    所以**不能**再拿它当兜底：player 请求"成功"会掩盖真正的下载失败。
    """
    seen_clients: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            seen_clients.append(kwargs["headers"]["X-Youtube-Client-Name"])
            return FakeResponse(200, json_data=_player())

    with patch("parsehub.provider_api.youtube_video.http.AsyncClient", FakeClient):
        video = asyncio.run(fetch_video("dQw4w9WgXcQ", proxy="http://127.0.0.1:1085"))

    assert seen_clients == ["101"]
    assert video.video_id == "dQw4w9WgXcQ"
    assert video.user_agent  # 带下载时用的 UA


def test_fetch_video_raises_when_every_client_fails():
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            raise RuntimeError("network down")

    with patch("parsehub.provider_api.youtube_video.http.AsyncClient", FakeClient):
        with pytest.raises(YoutubeVideoError):
            asyncio.run(fetch_video("dQw4w9WgXcQ"))


# ── 分离流的 mux 下载接线（离线, 打桩下载与 mux） ─────────────────────────


def test_split_stream_download_fetches_video_and_audio_then_muxes(tmp_path):
    video = parse_player_response(_player())
    selected = select_streams(video, allow_mux=True)
    result = YtbVideoParseResult(info=video, selected=selected)

    requested: list[str] = []

    async def fake_download(url, save_path, **_kwargs):
        requested.append(url)
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_bytes(b"x" * 128)
        return str(save_path)

    muxed: dict = {}

    async def fake_mux(video_path, audio_path, output_path):
        assert Path(video_path).exists() and Path(audio_path).exists()
        muxed["video"] = str(video_path)
        muxed["audio"] = str(audio_path)
        Path(output_path).write_bytes(b"v" * 4096)

    with (
        patch("parsehub.parsers.parser.youtube.download", new=fake_download),
        patch.object(YtbVideoParseResult, "_mux", staticmethod(fake_mux)),
    ):
        download_result = asyncio.run(result.download(tmp_path))

    assert requested == [selected.video_url, selected.audio_url]
    assert muxed["video"] != muxed["audio"]
    assert download_result.media.path.endswith(".mp4")
    assert Path(download_result.media.path).stat().st_size == 4096
    assert download_result.media.duration == 213


def test_muxed_stream_download_uses_the_single_file_path(tmp_path):
    """合一档 (itag 18) -> 直接下单个文件, 不经过 ffmpeg。"""
    video = parse_player_response(_player("youtube_player_android.json"))
    selected = select_streams(video, allow_mux=False)
    assert selected.audio_url is None
    result = YtbVideoParseResult(info=video, selected=selected)

    requested: list[str] = []

    async def fake_download(url, save_path, **_kwargs):
        requested.append(url)
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_bytes(b"x" * 2048)
        return str(save_path)

    # 合一档走基类 `ParseResult._do_download`, 它用的是 `types.result` 里绑定的 download
    with patch("parsehub.types.result.download", new=fake_download):
        download_result = asyncio.run(result.download(tmp_path))

    assert requested == [selected.video_url]
    assert download_result.media.height == 360
    assert Path(download_result.media.path).stat().st_size == 2048


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_real_ffmpeg_mux_produces_a_playable_mp4(tmp_path):
    """真跑一次 ffmpeg 合并: 生成的 mp4 里确实有一条视频流 + 一条音频流。"""
    video_in = tmp_path / "in_video.mp4"
    audio_in = tmp_path / "in_audio.m4a"
    out = tmp_path / "out.mp4"
    for cmd in (
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "1", "-y", str(video_in)],
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-y", str(audio_in)],
    ):
        subprocess.run(cmd, check=True, capture_output=True)

    asyncio.run(YtbVideoParseResult._mux(video_in, audio_in, out))

    assert out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "video" in probe and "audio" in probe


# ── ⑤ 帖子路径不受影响（回归） ────────────────────────────────────────────


def test_post_links_still_use_the_page_path_not_the_video_path():
    video_calls = {"n": 0}

    async def fake_fetch_video(*_args, **_kwargs):
        video_calls["n"] += 1
        raise AssertionError("帖子不该走视频路径")

    async def fake_fetch_post(url, **_kwargs):
        html = (FIXTURES / "youtube_post_single_image.html").read_text(encoding="utf-8")
        return parse_post_page(html, url=url)

    post_url = "https://www.youtube.com/post/Ugkxzq0QbgtS1VgHo8hIjZwpiwshZSvd-JtO"
    with (
        patch("parsehub.parsers.parser.youtube.fetch_video", new=fake_fetch_video),
        patch("parsehub.parsers.parser.youtube.fetch_post", new=fake_fetch_post),
    ):
        result = asyncio.run(YtbParse().parse(post_url))

    assert video_calls["n"] == 0
    assert isinstance(result, MultimediaParseResult)
    assert result.author_name == "ANIPLUS Asia"


@pytest.mark.parametrize(
    "url",
    [
        VIDEO_URL,
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/hnSeq_P3jAo",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/post/Ugkxzq0QbgtS1VgHo8hIjZwpiwshZSvd-JtO",
    ],
)
def test_youtube_links_still_match(url):
    assert YtbParse.match(url)
