from pathlib import Path

import pytest

from parsehub import ParseHub, Platform
from parsehub.parsers.parser.pixiv import PixivParser, PixivParseResult
from parsehub.provider_api.pixiv import (
    REFERER,
    UA,
    Pixiv,
    PixivError,
    PixivIllust,
    _master_size,
    _parse_image,
)
from parsehub.types import ImageRef
from parsehub.types import result as result_mod

# 结构抄自 pixiv /ajax/illust/<id> 的真实响应（只保留断言用到的字段）
ILLUST_BODY = {
    "illustId": "95276699",
    "illustTitle": "Happy New Year from Xinyan!",
    "userId": "71948315",
    "userName": "Kayden Lockes",
    "createDate": "2022-01-03T22:27:47+09:00",
    "pageCount": 2,
    "xRestrict": 0,
    "description": (
        'TWITTER: <strong><a href="https://twitter.com/KaydenLockes" target="_blank">twitter/KaydenLockes</a>'
        "</strong><br /><br />late 5k milestone Art <br /><br />"
        '<a href="/jump.php?https%3A%2F%2Fwww.patreon.com%2FKaydenLockes" target="_blank">'
        "https://www.patreon.com/KaydenLockes</a><br />"
    ),
    "tags": {"tags": [{"tag": "Genshin Impact"}, {"tag": "Xinyan"}]},
}

MASTER_PREFIX = "https://i.pximg.net/img-master/img/2022/01/03/22/27/47"
PAGES_BODY = [
    {
        "urls": {
            "original": "https://i.pximg.net/img-original/img/2022/01/03/22/27/47/95276699_p0.jpg",
            "thumb_mini": "https://i.pximg.net/c/128x128/img-master/img/2022/01/03/22/27/47/95276699_p0_square1200.jpg",
        },
        "width": 1200,
        "height": 1697,
    },
    {
        "urls": {
            "original": "https://i.pximg.net/img-original/img/2022/01/03/22/27/47/95276699_p1.png",
            "thumb_mini": "https://i.pximg.net/c/128x128/img-master/img/2022/01/03/22/27/47/95276699_p1_square1200.jpg",
        },
        "width": 1200,
        "height": 1697,
    },
]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pixiv.net/artworks/95276699",
        "https://pixiv.net/artworks/95276699",
        "https://www.pixiv.net/en/artworks/95276699",
        "https://www.pixiv.net/member_illust.php?mode=medium&illust_id=95276699",
    ],
)
def test_get_illust_id_accepts_supported_urls(url):
    assert Pixiv.get_illust_id(url) == "95276699"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pixiv.net/novel/show.php?id=123",
        "https://www.pixiv.net/users/71948315",
    ],
)
def test_get_illust_id_rejects_unsupported_urls(url):
    with pytest.raises(PixivError):
        Pixiv.get_illust_id(url)


def test_parse_maps_illust_and_pages():
    illust = PixivIllust.parse(ILLUST_BODY, PAGES_BODY)
    assert illust.illust_id == "95276699"
    assert illust.title == "Happy New Year from Xinyan!"
    assert illust.author_name == "Kayden Lockes"
    assert illust.author_id == "71948315"
    assert illust.tags == ["Genshin Impact", "Xinyan"]
    assert illust.page_count == 2
    assert illust.is_r18 is False
    # 原图长边超过 1200 -> 换成 master1200 版本 (原图会让 Telegram 抓取失败)
    assert [i.url for i in illust.images] == [
        f"{MASTER_PREFIX}/95276699_p0_master1200.jpg",
        f"{MASTER_PREFIX}/95276699_p1_master1200.jpg",
    ]
    # thumb 取小图，且不与自己相同
    assert illust.images[0].thumb_url == PAGES_BODY[0]["urls"]["thumb_mini"]


def test_images_carry_dimensions_and_real_ext():
    """尺寸按 master1200 的长边 1200 缩放; master1200 恒为 jpg"""
    illust = PixivIllust.parse(ILLUST_BODY, PAGES_BODY)
    assert [(i.width, i.height) for i in illust.images] == [(849, 1200), (849, 1200)]
    assert [i.ext for i in illust.images] == ["jpg", "jpg"]


def test_oversized_original_is_downgraded_to_master1200():
    """实测 Telegram 抓 6000x6000 / 7MB 的原图会稳定失败 (failed to get HTTP URL content)"""
    huge = {
        "urls": {
            "original": "https://i.pximg.net/img-original/img/2026/09/12/16/00/06/149574390_p0.jpg",
            "thumb_mini": "https://i.pximg.net/c/128x128/img-master/img/2026/09/12/16/00/06/149574390_p0_square1200.jpg",
        },
        "width": 6000,
        "height": 6000,
    }
    image = _parse_image(huge)
    assert image is not None
    assert image.url == "https://i.pximg.net/img-master/img/2026/09/12/16/00/06/149574390_p0_master1200.jpg"
    assert (image.width, image.height) == (1200, 1200)
    assert image.ext == "jpg"
    # 列表缩略图仍然是 128x128 的小图
    assert image.thumb_url == huge["urls"]["thumb_mini"]


def test_small_original_is_left_alone():
    """本来就小的图不替换: 小作品的 master1200 未必存在"""
    small = {
        "urls": {"original": "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/12345_p0.png"},
        "width": 800,
        "height": 600,
    }
    image = _parse_image(small)
    assert image is not None
    assert image.url == "https://i.pximg.net/img-original/img/2020/01/01/00/00/00/12345_p0.png"
    assert (image.width, image.height) == (800, 600)
    assert image.ext == "png"


def test_master_size_uses_long_edge():
    assert _master_size(6000, 6000) == (1200, 1200)
    assert _master_size(1968, 2664) == (887, 1200)
    assert _master_size(800, 600) == (800, 600)


def test_parse_strips_description_html():
    illust = PixivIllust.parse(ILLUST_BODY, PAGES_BODY)
    assert "<br" not in illust.description
    assert "<strong>" not in illust.description
    # 链接文本本身就是可读 URL，不需要还原 jump.php
    assert "https://www.patreon.com/KaydenLockes" in illust.description
    assert "late 5k milestone Art" in illust.description
    assert "\n\n\n" not in illust.description


def test_parse_raises_when_images_are_restricted():
    """R-18 作品对未登录用户不返回 urls，应给出可操作的报错"""
    r18 = dict(ILLUST_BODY, xRestrict=1)
    with pytest.raises(PixivError, match="cookie"):
        PixivIllust.parse(r18, [{"urls": {"original": None}}])


def test_parser_is_registered_and_matches_artwork_url():
    parsers = {p.__platform__: p for p in ParseHub().parsers}
    assert Platform.PIXIV in parsers
    assert parsers[Platform.PIXIV] is PixivParser
    assert PixivParser.match("https://www.pixiv.net/artworks/95276699")
    assert not PixivParser.match("https://www.pixiv.net/novel/show.php?id=123")


def test_parser_forwards_dimensions_to_image_ref(monkeypatch):
    """inline 的 InlineQueryResultPhoto 直接用 ImageRef.width/height 当 photo_width/photo_height,
    为 0 时 Telegram 无法渲染缩略图, 所以这一层必须把尺寸带出去"""
    import parsehub.parsers.parser.pixiv as parser_mod

    async def fake_parse(self, url):  # noqa: ANN001, ARG001
        return PixivIllust.parse(ILLUST_BODY, PAGES_BODY)

    monkeypatch.setattr(parser_mod.Pixiv, "parse", fake_parse)
    result = ParseHub().parse_sync("https://www.pixiv.net/artworks/95276699")

    assert [(m.width, m.height) for m in result.media] == [(849, 1200), (849, 1200)]
    assert [m.ext for m in result.media] == ["jpg", "jpg"]
    assert all(m.thumb_url for m in result.media)


def test_parser_marks_r18_illust_as_sensitive(monkeypatch):
    """pixiv 的 xRestrict>0 即 R-18, 要作为打码标记传给 result"""
    import parsehub.parsers.parser.pixiv as parser_mod

    async def fake_parse(self, url):  # noqa: ANN001, ARG001
        return PixivIllust.parse(dict(ILLUST_BODY, xRestrict=1), PAGES_BODY)

    monkeypatch.setattr(parser_mod.Pixiv, "parse", fake_parse)
    result = ParseHub().parse_sync("https://www.pixiv.net/artworks/95276699")
    assert result.is_sensitive is True


def test_parser_leaves_plain_illust_unmarked(monkeypatch):
    """非 R-18 不置位, 避免误打码"""
    import parsehub.parsers.parser.pixiv as parser_mod

    async def fake_parse(self, url):  # noqa: ANN001, ARG001
        return PixivIllust.parse(ILLUST_BODY, PAGES_BODY)

    monkeypatch.setattr(parser_mod.Pixiv, "parse", fake_parse)
    result = ParseHub().parse_sync("https://www.pixiv.net/artworks/95276699")
    assert result.is_sensitive is False


def test_download_sends_pixiv_referer(monkeypatch, tmp_path):
    """i.pximg.net 对不带 Referer 的下载返回 403 (实测), 所以下载必须自己带上 Referer"""
    captured: dict = {}

    async def fake_download(url, save_path, **kwargs):  # noqa: ANN001, ANN003, ANN202, ARG001
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        Path(save_path).write_bytes(b"fake-image")
        return str(save_path)

    monkeypatch.setattr(result_mod, "download", fake_download)
    result = PixivParseResult(
        title="t",
        media=[ImageRef(url="https://i.pximg.net/img-original/img/x_p0.jpg", width=1, height=1)],
    )
    result.download_sync(tmp_path)

    assert captured["headers"] == {"User-Agent": UA, "Referer": REFERER}
    assert captured["url"].startswith("https://i.pximg.net/")
