"""外链卡片的预览图：图在 API 里，但不在 `entities.media` 那条路上。

起因（2026-10-06，用户）:「这个里面有图，没抓到，我看是外网的图，api里面有吗？」
实际是群里的 `OtakuLabJP/status/2107314643257700461`: 推文正文只贴了个链接
(`https://0115765.com/archives/208357`), `legacy.entities.media` 是**空**的,
于是媒体数 0、日志 `files=0`。而 API 里其实有图 —— X 给那个网页生成的卡片图,
在 `node["card"].legacy.binding_values` 里 (托管在 `pbs.twimg.com/card_img/...`)。

结构取自真实响应(已落盘 `/tmp/raw_OtakuLab.json`), 只保留必要字段。
"""

from parsehub.provider_api.twitter import Twitter

CARD_IMG = "https://pbs.twimg.com/card_img/2107314611443879936/KtNXXpoz"

def _image(key: str, width: int, height: int, suffix: str) -> dict:
    """真实响应里的一个尺寸变体。"""
    return {
        "key": key,
        "value": {"type": "IMAGE", "image_value": {"height": height, "width": width, "url": f"{CARD_IMG}?{suffix}"}},
    }


#: 真实响应的 binding_values（同一张图十几个尺寸变体, 只留几个有代表性的）
CARD_VALUES = [
    _image("photo_image_full_size_large", 800, 419, "format=jpg&name=800x419"),
    _image("thumbnail_image", 280, 150, "format=jpg&name=280x150"),
    {"key": "title", "value": {"type": "STRING", "string_value": "【秋アニメ】…【先行カット】 | オタク総研"}},
    {"key": "domain", "value": {"type": "STRING", "string_value": "0115765.com"}},
    _image("summary_photo_image_original", 1600, 900, "format=jpg&name=orig"),
    _image("summary_photo_image_small", 386, 202, "format=jpg&name=386x202"),
]

TEXT = "小説と漫画で99割違う異色作、今夜スタート\nhttps://0115765.com/archives/208357"


def _result(
    *,
    card_name: str | None = "summary_large_image",
    card_values: list | None = None,
    media: list | None = None,
) -> dict:
    legacy: dict = {
        "full_text": TEXT,
        "created_at": "Tue Oct 06 00:00:00 +0000 2026",
        "favorite_count": 12,
        "entities": {"urls": [], "media": media or []},
    }
    node: dict = {
        "rest_id": "2107314643257700461",
        "legacy": legacy,
        "core": {"user_results": {"result": {"legacy": {"name": "オタク総研", "screen_name": "OtakuLabJP"}}}},
        "views": {"count": "1000"},
    }
    if card_name is not None:
        node["card"] = {
            "rest_id": "card-1",
            "legacy": {
                "name": card_name,
                "url": "https://t.co/5dEi7fnkJg",
                "binding_values": CARD_VALUES if card_values is None else card_values,
            },
        }
    return node


def _parse(node: dict):
    return Twitter()._parse_result(node)


def test_a_link_card_photo_becomes_media():
    """正文里只有链接的推文: 卡片图必须被当成媒体收下（以前一张都没有）"""
    tweet = _parse(_result())
    assert tweet.media, "卡片图应该出现在媒体里"
    assert len(tweet.media) == 1
    assert "card_img/2107314611443879936/KtNXXpoz" in tweet.media[0].url


def test_the_largest_variant_is_picked():
    """十几个尺寸变体里取 ``name=orig`` 那张, 不是 800x419 / 280x150 那些"""
    tweet = _parse(_result())
    assert tweet.media[0].url.endswith("name=orig"), tweet.media[0].url


def test_the_card_photo_carries_its_size():
    """宽高从 image_value 里取（卡片图有真实尺寸, 别写 0）"""
    tweet = _parse(_result())
    assert (tweet.media[0].width, tweet.media[0].height) == (1600, 900)


def test_a_player_card_is_left_to_the_youtube_path():
    """播放器卡片（YouTube 等）跳过 —— 那条路走 oembed 拿真封面, 再加一张就重了"""
    tweet = _parse(_result(card_name="player"))
    assert not tweet.media, tweet.media


def test_a_card_without_an_image_adds_nothing():
    """只有文字的外链卡片（没有 image_value）不该造出媒体"""
    strings_only = [
        {"key": "title", "value": {"type": "STRING", "string_value": "标题"}},
        {"key": "domain", "value": {"type": "STRING", "string_value": "example.com"}},
    ]
    tweet = _parse(_result(card_values=strings_only))
    assert not tweet.media, tweet.media


def test_no_card_means_no_extra_media():
    """没有 card 的推文（如 sudachi_anime 那条）行为不变"""
    tweet = _parse(_result(card_name=None))
    assert not tweet.media, tweet.media


def test_the_tweets_own_media_comes_first():
    """推文自带媒体在前、卡片图在后（与 X 上的显示顺序一致）"""
    own = [
        {
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/HT7qfOXbcAA9PsK.jpg",
            "original_info": {"width": 1200, "height": 675},
        }
    ]
    tweet = _parse(_result(media=own))
    assert len(tweet.media) == 2, tweet.media
    assert "media/HT7qfOXbcAA9PsK" in tweet.media[0].url, tweet.media[0].url
    assert "card_img/" in tweet.media[1].url, tweet.media[1].url


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
