"""「统一卡片」(``unified_card``) 里的媒体 —— 视频常常**只**在这里。

起因（2026-10-07，用户）:「https://x.com/Apple/status/2104922864910815586 抓不到视频」

那条推文的 ``legacy`` 上**只有 ``entities``**（连 ``extended_entities`` 都没有），
里面一个 media 都没有 —— 视频全在 ``card.legacy.binding_values`` 里那个
``unified_card`` **JSON 字符串**的 ``media_entities`` 里（``amplify_video``）。
以前只读 ``entities.media``，于是这条推文发出来只剩正文，视频整段丢失。

fixture ``twitter_unified_card.json`` 是真实响应（删掉了 ``ext`` 调色板等噪音）。
"""

import json
from pathlib import Path

from parsehub.provider_api.twitter import Twitter

FIXTURE = Path(__file__).parent / "fixtures" / "twitter_unified_card.json"
TWEET_ID = "2104922864910815586"

#: 真实响应里那三档 mp4（顺序照原样：950k → 2176k → 632k）
VARIANTS = [
    {
        "content_type": "application/x-mpegURL",
        "url": "https://video.twimg.com/amplify_video/1/pl/x.m3u8?tag=14",
    },
    {
        "bitrate": 950000,
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/amplify_video/1/vid/avc1/480x600/mid.mp4?tag=14",
    },
    {
        "bitrate": 2176000,
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/amplify_video/1/vid/avc1/720x900/best.mp4?tag=14",
    },
    {
        "bitrate": 632000,
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/amplify_video/1/vid/avc1/320x400/worst.mp4?tag=14",
    },
]


def _result(*, media_entities=None, card_name: str = "unified_card", raw: str | None = None) -> dict:
    """真实结构的精简版：``unified_card`` 是一段 JSON 字符串。"""
    if raw is None:
        payload = {
            "type": "video_website",
            "component_objects": {
                "details_1": {"type": "details", "data": {"subtitle": {"content": "apple.com"}}},
                "media_1": {"type": "media", "data": {"id": "13_2104922396662874112"}},
            },
            "destination_objects": {
                "browser_with_docked_media_1": {
                    "type": "browser_with_docked_media",
                    "data": {"url_data": {"url": "https://www.apple.com/hk/iphone-18-pro/", "vanity": "apple.com"}},
                }
            },
            "media_entities": media_entities
            if media_entities is not None
            else {
                "13_2104922396662874112": {
                    "type": "video",
                    "media_url_https": "https://pbs.twimg.com/amplify_video_thumb/1/img/abc.jpg",
                    "media_key": "13_2104922396662874112",
                    "original_info": {"width": 1440, "height": 1800},
                    "video_info": {"duration_millis": 10000, "variants": VARIANTS},
                }
            },
            "components": ["media_1", "details_1"],
        }
        raw = json.dumps(payload, ensure_ascii=False)
    return {
        "rest_id": TWEET_ID,
        "legacy": {
            "full_text": "用 iPhone 18 Pro 全新相機控制，影相拍片更出色。",
            "created_at": "Tue Oct 06 00:00:00 +0000 2026",
            "entities": {"urls": [], "media": []},
        },
        "core": {"user_results": {"result": {"legacy": {"name": "Apple", "screen_name": "Apple"}}}},
        "views": {"count": "1000"},
        "card": {
            "rest_id": "card-1",
            "legacy": {
                "name": card_name,
                "binding_values": [{"key": "unified_card", "value": {"type": "STRING", "string_value": raw}}],
            },
        },
    }


def _parse(node: dict):
    return Twitter()._parse_result(node)


# ---------------------------------------------------------------- 真实响应

def test_the_real_response_yields_its_video():
    """**核心**: 用户报的那条推文 —— 媒体数必须是 1（以前是 0，视频整段丢失）"""
    tweet = Twitter().parse(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert len(tweet.media) == 1, tweet.media
    assert tweet.media[0].url.endswith("nZUAXIBlF33JkOqB.mp4?tag=14"), tweet.media[0].url


def test_the_real_response_body_still_reads():
    """正文照旧（不能因为多接一条媒体通道把正文弄丢）"""
    tweet = Twitter().parse(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert tweet.full_text == "用 iPhone 18 Pro 全新相機控制，影相拍片更出色。"
    assert tweet.author_handle == "Apple"
    assert tweet.view_count == 410225  # 快照那一刻的真实浏览量


def test_the_highest_bitrate_variant_wins():
    """**核心**: 取**最高码率**那档 —— 实测 variants 顺序是 950k → 2176k → 632k，

    ``variants[-1]`` 会拿到**最糊的 632k**（用户看到的就是糊的）。
    """
    tweet = _parse(_result())
    assert tweet.media[0].url.endswith("/720x900/best.mp4?tag=14"), tweet.media[0].url
    assert tweet.media[0].url != "https://video.twimg.com/amplify_video/1/vid/avc1/320x400/worst.mp4?tag=14"


def test_the_video_carries_its_size_and_thumbnail():
    tweet = _parse(_result())
    assert (tweet.media[0].width, tweet.media[0].height) == (1440, 1800)
    assert "amplify_video_thumb" in tweet.media[0].thumb_url


def test_the_unified_card_is_not_read_as_a_link_preview():
    """``unified_card`` 的媒体由统一卡片那条路取 —— 卡片预览图那条路要跳过它，

    否则同一张图会进两次。
    """
    node = _result()
    node["card"]["legacy"]["binding_values"].append(
        {"key": "photo_image_full_size_original", "value": {"type": "IMAGE", "image_value": {"url": "https://x/y.jpg"}}}
    )
    tweet = _parse(node)
    assert len(tweet.media) == 1, tweet.media


# ---------------------------------------------------------------- 码率选取

def test_a_video_without_an_mp4_falls_back_to_the_playlist():
    """只有 m3u8（没有 mp4）时回退最后一个变体 —— 不能返回空串把媒体丢掉"""
    only_hls = [{"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/pl/x.m3u8"}]
    assert Twitter._best_video_url(only_hls) == "https://video.twimg.com/pl/x.m3u8"


def test_an_unknown_bitrate_field_still_compares():
    """长文章那条路的 variants 用 ``bit_rate``（带下划线）—— 两种都要认"""
    variants = [
        {"content_type": "video/mp4", "bit_rate": 100, "url": "https://a/low.mp4"},
        {"content_type": "video/mp4", "bit_rate": 900, "url": "https://a/high.mp4"},
    ]
    assert Twitter._best_video_url(variants) == "https://a/high.mp4"


def test_no_variants_yields_nothing():
    assert Twitter._best_video_url([]) == ""


# ---------------------------------------------------------------- 回归与容错

def test_the_tweets_own_media_still_works():
    """**回归**: ``legacy.entities.media`` 那条路照旧（照片 / 视频 / 动图）"""
    own = [
        {
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/AAA.jpg",
            "original_info": {"width": 1200, "height": 675},
        },
        {
            "type": "video",
            "media_url_https": "https://pbs.twimg.com/media/BBB.jpg",
            "original_info": {"width": 1280, "height": 720},
            "video_info": {"duration_millis": 5000, "variants": VARIANTS},
        },
        {
            "type": "animated_gif",
            "media_url_https": "https://pbs.twimg.com/media/CCC.jpg",
            "original_info": {"width": 480, "height": 270},
            "video_info": {"variants": [{"content_type": "video/mp4", "url": "https://v/gif.mp4"}]},
        },
    ]
    empty = _result(media_entities={})
    assert not _parse(empty).media, "空的 media_entities 不该造出媒体"

    node = _result(media_entities={})
    node["legacy"]["entities"] = {"urls": [], "media": own}
    tweet = _parse(node)
    assert len(tweet.media) == 3, tweet.media
    assert tweet.media[0].url.endswith("AAA.jpg?name=orig"), tweet.media[0].url
    assert tweet.media[1].url.endswith("/720x900/best.mp4?tag=14"), tweet.media[1].url
    assert tweet.media[2].url == "https://v/gif.mp4"


def test_a_broken_payload_is_skipped_not_fatal():
    """``unified_card`` 不是合法 JSON（X 改版 / 截断）时跳过，**别让整条解析失败**"""
    tweet = _parse(_result(raw="{不是 json"))
    assert tweet.full_text, "正文照旧"
    assert not tweet.media, tweet.media


def test_a_missing_media_entity_field_is_skipped_too():
    """媒体对象缺字段（没有 ``video_info``）时跳过那一项，不看崩"""
    tweet = _parse(_result(media_entities={"13_1": {"type": "video"}}))
    assert not tweet.media, tweet.media


def test_a_list_shaped_media_entities_is_accepted():
    """``media_entities`` 万一给成数组也能吃（X 两种都出现过）"""
    raw = _result()["card"]["legacy"]["binding_values"][0]["value"]["string_value"]
    entries = list(json.loads(raw)["media_entities"].values())
    tweet = _parse(_result(media_entities=entries))
    assert len(tweet.media) == 1, tweet.media
    assert tweet.media[0].url.endswith("/720x900/best.mp4?tag=14"), tweet.media[0].url


def test_another_card_type_is_untouched():
    """别的卡片类型（如 ``summary_large_image``）不归这条路管"""
    node = _result()
    node["card"]["legacy"]["name"] = "summary_large_image"
    assert not _parse(node).media, _parse(node).media


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
