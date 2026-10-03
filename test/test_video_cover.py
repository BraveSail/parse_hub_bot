"""视频封面: 缩放成 Telegram 缩略图 + 只给视频类准备封面。"""

import asyncio
import io
from pathlib import Path

from parsehub.types import AniRef, ImageRef, VideoRef
from PIL import Image

from plugins.parse import covers
from plugins.parse.covers import prepare_video_thumbs, shrink_to_thumbnail


def _png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def test_shrink_respects_telegram_limits():
    """边长要降到 320 以内, 体积要小于 200 KB"""
    out = shrink_to_thumbnail(_png((1920, 1080)))
    assert out is not None
    assert len(out) < 200 * 1024
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 320


def test_shrink_keeps_small_image():
    out = shrink_to_thumbnail(_png((100, 60)))
    assert out is not None
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (100, 60)


def test_shrink_rejects_non_image():
    """拿到 HTML 错误页之类的东西时不能炸, 返回 None 由调用方降级"""
    assert shrink_to_thumbnail(b"<html>403</html>") is None


def test_shrink_rejects_truncated_image():
    data = _png((800, 600))
    assert shrink_to_thumbnail(data[: len(data) // 3]) is None


def test_prepare_only_covers_videos(monkeypatch, tmp_path):
    """图片不需要封面; 视频/实况/动画才下载"""
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        path = Path(tmp_path) / "t.jpg"
        path.write_bytes(_png((320, 180)))
        return path

    monkeypatch.setattr(covers, "fetch_video_thumb", fake_fetch)
    refs = [
        ImageRef(url="https://x/img.jpg", thumb_url="https://x/img_thumb.jpg"),
        VideoRef(url="https://x/v.mp4", thumb_url="https://x/v_thumb.jpg"),
        AniRef(url="https://x/a.gif", thumb_url="https://x/a_thumb.jpg"),
    ]
    thumbs = asyncio.run(prepare_video_thumbs(refs))
    assert set(calls) == {"https://x/v_thumb.jpg", "https://x/a_thumb.jpg"}
    assert "https://x/img_thumb.jpg" not in thumbs


def test_prepare_dedupes_and_skips_missing(monkeypatch, tmp_path):
    """同一封面只下一次; 下载失败的条目缺席 (不能让发送失败)"""
    calls: list[str] = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        if "bad" in url:
            return None
        path = Path(tmp_path) / "t.jpg"
        path.write_bytes(_png((320, 180)))
        return path

    monkeypatch.setattr(covers, "fetch_video_thumb", fake_fetch)
    refs = [
        VideoRef(url="https://x/1.mp4", thumb_url="https://x/same.jpg"),
        VideoRef(url="https://x/2.mp4", thumb_url="https://x/same.jpg"),
        VideoRef(url="https://x/3.mp4", thumb_url="https://x/bad.jpg"),
        VideoRef(url="https://x/4.mp4"),
    ]
    thumbs = asyncio.run(prepare_video_thumbs(refs))
    assert calls == ["https://x/same.jpg", "https://x/bad.jpg"]
    assert list(thumbs) == ["https://x/same.jpg"]


def test_fetch_returns_none_on_http_error(monkeypatch):
    """HTTP 非 200 时静默返回 None"""

    class FakeResponse:
        status_code = 404
        content = b""

    class FakeClient:
        def __init__(self, **kwargs): ...
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(covers.http, "AsyncClient", FakeClient)
    assert asyncio.run(covers.fetch_video_thumb("https://x/nope.jpg")) is None
