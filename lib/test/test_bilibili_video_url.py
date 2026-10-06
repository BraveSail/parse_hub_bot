"""B 站视频地址：**用 B 站给的首选地址，不改域名**。

起因（2026-10-06 实测，用户报「速度很慢」）: 代码以前取 ``backup_url[0]`` 并把域名
改写成 ``upos-sz-upcdnbda2.bilivideo.com``。两个问题:

1. **``durl.url`` 才是官方首选** —— 同一条视频两条地址指向不同 CDN，实测
   ``durl.url`` 0.93 MB/s vs ``backup_url``(akamai) 27.96 MB/s（差 30 倍）；
2. **改写域名是伪造签名** —— B 站的播放签名与 host 绑定，把 path 换到别的域名实测
   403 / 超时（换域名能跑只是兼容性侥幸）。
"""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.parsers.parser.bilibili import BiliParse
from parsehub.provider_api.bilibili import BiliAPI

INFO = {
    "data": {
        "View": {
            "cid": 111,
            "duration": 198,
            "dimension": {"width": 1914, "height": 1080},
            "desc": "简介",
            "title": "标题",
            "pic": "https://i2.hdslb.com/x.jpg",
            "pubdate": 1791208244,
            "owner": {"mid": 224267770, "name": "作者"},
            "stat": {"view": 100},
        }
    }
}

#: 官方首选地址与备用地址（指向不同 CDN）
PREFERRED = "https://upos-sz-mirrorcosov.bilivideo.com/upgcxcode/1/path.mp4?e=sig"
BACKUP = "https://upos-hz-mirrorakam.akamaized.net/upgcxcode/1/path.mp4?e=sig"


def _parse(playurl_payload: dict):
    async def fake_video_info(*args, **kwargs):  # noqa: ANN002, ANN003
        return INFO

    with (
        patch.object(BiliAPI, "get_video_info", new=AsyncMock(side_effect=fake_video_info), create=True),
        patch.object(BiliAPI, "get_buvid", new=AsyncMock(return_value=("b3", "b4"))),
        patch.object(BiliAPI, "get_video_playurl", new=AsyncMock(return_value=playurl_payload)),
    ):
        return asyncio.run(BiliParse()._do_parse("https://www.bilibili.com/video/BV1q4ab6FECm"))


def test_the_preferred_url_is_used_not_the_backup():
    result = _parse({"data": {"durl": [{"url": PREFERRED, "backup_url": [BACKUP]}]}})
    assert result.media.url == PREFERRED, result.media.url


def test_the_host_is_left_alone():
    """域名必须保持 B 站给的原样 —— 改写域名 = 伪造签名"""
    result = _parse({"data": {"durl": [{"url": PREFERRED, "backup_url": [BACKUP]}]}})
    assert "upos-sz-mirrorcosov.bilivideo.com" in result.media.url
    assert "upcdnbda2" not in result.media.url
    assert "akamaized" not in result.media.url


def test_the_parse_still_works_when_there_is_no_backup():
    result = _parse({"data": {"durl": [{"url": PREFERRED}]}})
    assert result.media.url == PREFERRED


def test_change_source_is_gone():
    """那个改域名的函数已删除（防止有人把它加回来）"""
    assert not hasattr(BiliParse, "change_source")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
