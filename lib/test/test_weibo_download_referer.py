"""微博媒体下载必须带 Referer。

实测（2026-10-10，161 生产容器，curl_cffi chrome150，唯一变量=Referer）：

| 请求 | 结果 |
| --- | --- |
| 无 Referer（生产形态）| **403**（火山引擎 CDN: ``x-ban: MISS`` / ``deny code 68``）|
| ``Referer: https://weibo.com`` | 200 / 206 全量 |

图床（``*.sinaimg.cn``）与视频 CDN（``*.weibocdn.com``）都如此。
修复形态与 pixiv / douban / douyin 一致：结果是**平台子类**，在 ``_do_download``
里注入 Referer。

⚠️ **必须是子类**：结果层缓存按类名（``impl``）重建 —— 若退回通用
``ImageParseResult``，缓存命中时子类的 ``_do_download`` 不再执行，下载又会 403
（pixiv 2026-10-05 踩过同一个坑，见 ``test_result_roundtrip.py`` 的对照测试）。

fixture：``weibo_image_post.json`` / ``weibo_video_post.json`` —— 真实详情 API 响应
裁剪（只留断言用到的字段）。**URL 带真实签名**（``ssig``）但不参与请求。
"""

import asyncio
import json
from pathlib import Path

import pytest

from parsehub.parsers.parser.weibo import (
    REFERER,
    WeiboImageParseResult,
    WeiboMultimediaParseResult,
    WeiboParser,
    WeiboParseResult,
    WeiboVideoParseResult,
)
from parsehub.provider_api.weibo import WeiboAPI
from parsehub.types import ImageRef, ParseResult, VideoRef
from parsehub.types import result as result_mod

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fake_statuses_show(body: dict):
    async def fake(self, bid):  # noqa: ANN001, ARG001
        return body

    return fake


def test_image_download_sends_weibo_referer(monkeypatch, tmp_path):
    """图床（sinaimg.cn）无 Referer 时 403（实测），下载必须自己带上。"""
    captured: dict = {}

    async def fake_download(url, save_path, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        Path(save_path).write_bytes(b"fake-image")
        return str(save_path)

    monkeypatch.setattr(result_mod, "download", fake_download)
    result = WeiboImageParseResult(
        content="分享图片",
        photo=[
            ImageRef(
                url="https://wx3.sinaimg.cn/large/70745653ly1ihxa54f4bjj20m9091gr6.jpg",
                width=801,
                height=325,
            )
        ],
    )
    asyncio.run(result._do_download(output_dir=tmp_path))

    assert captured["headers"] == {"Referer": REFERER}
    assert captured["url"].startswith("https://wx3.sinaimg.cn/")


def test_video_download_sends_weibo_referer(monkeypatch, tmp_path):
    """视频 CDN（f.video.weibocdn.com）同样查 Referer（实测：无 Referer 403）。"""
    captured: dict = {}

    async def fake_download(url, save_path, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        captured["headers"] = kwargs.get("headers")
        Path(save_path).write_bytes(b"fake-video")
        return str(save_path)

    monkeypatch.setattr(result_mod, "download", fake_download)
    result = WeiboVideoParseResult(
        content="",
        video=VideoRef(
            url="https://f.video.weibocdn.com/o0/example.mp4?label=mp4_720p",
            width=1280,
            height=720,
            duration=25,
        ),
    )
    asyncio.run(result._do_download(output_dir=tmp_path))

    assert captured["headers"] == {"Referer": REFERER}


def test_multimedia_download_sends_weibo_referer(monkeypatch, tmp_path):
    """图文混排（视频 + 图）也走同一条注入路径。"""
    captured: list = []

    async def fake_download(url, save_path, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        captured.append(kwargs.get("headers"))
        Path(save_path).write_bytes(b"fake")
        return str(save_path)

    monkeypatch.setattr(result_mod, "download", fake_download)
    result = WeiboMultimediaParseResult(
        media=[
            ImageRef(url="https://wx3.sinaimg.cn/large/a.jpg", width=1, height=1),
            VideoRef(url="https://f.video.weibocdn.com/o0/b.mp4", width=1, height=1, duration=1),
        ],
    )
    asyncio.run(result._do_download(output_dir=tmp_path))

    assert captured == [{"Referer": REFERER}, {"Referer": REFERER}]


@pytest.mark.parametrize(
    ("cls", "expected_type"),
    [
        (WeiboImageParseResult, "image"),
        (WeiboVideoParseResult, "video"),
        (WeiboMultimediaParseResult, "multimedia"),
    ],
)
def test_weibo_subclasses_keep_their_post_type(cls, expected_type):
    """子类继承顺序不能把 ``type`` 改掉 —— 渲染层/缓存按它分派。"""
    assert cls.type.value == expected_type


def test_all_weibo_subclasses_share_the_referer_download():
    """三个子类的 ``_do_download`` 都解析到带 Referer 的那个实现。"""
    for cls in (WeiboImageParseResult, WeiboVideoParseResult, WeiboMultimediaParseResult):
        assert isinstance(cls, type) and issubclass(cls, WeiboParseResult)
        assert cls._do_download.__qualname__.startswith("WeiboParseResult."), cls.__name__


def test_cache_roundtrip_keeps_the_weibo_subclass():
    """**缓存往返必须还原子类** —— 退回通用类 = 缓存命中时下载又 403。"""
    from parsehub.types.serialize import result_from_cache_dict, result_to_cache_dict

    result = WeiboImageParseResult(
        content="分享图片",
        photo=[ImageRef(url="https://wx3.sinaimg.cn/large/a.jpg", width=1, height=1)],
    )
    result.raw_url = "https://weibo.com/1886672467/Rm1w5iaAD"

    back = result_from_cache_dict(result_to_cache_dict(result))

    assert type(back) is WeiboImageParseResult, f"子类丢了: {type(back).__name__}"
    assert type(back)._do_download is WeiboParseResult._do_download


def test_plain_parse_result_has_no_weibo_referer():
    """通用类不受影响（防止把注入写进基类）。"""
    assert ParseResult._do_download.__qualname__.startswith("ParseResult.")


def test_image_post_parses_to_a_weibo_subclass(monkeypatch):
    """真实图微博 fixture → 产出必须是 ``WeiboImageParseResult``。"""
    body = _load("weibo_image_post.json")
    monkeypatch.setattr(WeiboAPI, "statuses_show", _fake_statuses_show(body))

    result = asyncio.run(WeiboParser().parse("https://weibo.com/1886672467/Rm1w5iaAD"))

    assert type(result) is WeiboImageParseResult, f"实际: {type(result).__name__}（下载将不带 Referer）"
    assert len(result.media) == 2, f"media: {result.media}"


def test_video_post_parses_to_a_weibo_subclass(monkeypatch):
    """真实视频微博 fixture → 产出必须是 ``WeiboVideoParseResult``。

    视频那条走的是 ``page_info.object_type == video`` 分支（与图分支不同的构造点）——
    只测图分支的话，这个构造点漏改不会被发现。
    """
    body = _load("weibo_video_post.json")
    monkeypatch.setattr(WeiboAPI, "statuses_show", _fake_statuses_show(body))

    result = asyncio.run(WeiboParser().parse("https://weibo.com/1135787567/RhvyqEuTH"))

    assert type(result) is WeiboVideoParseResult, f"实际: {type(result).__name__}（下载将不带 Referer）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
