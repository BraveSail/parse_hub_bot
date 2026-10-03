"""各平台元数据 (发布时间 / 浏览量) 的离线提取用例 —— 字段形状对标真实响应."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from parsehub.parsers.base.ytdlp import YtVideoInfo
from parsehub.parsers.parser.bilibili import BiliParse
from parsehub.parsers.parser.douyin import DouyinApiResult, DouyinMediaType
from parsehub.parsers.parser.facebook import FacebookParse
from parsehub.parsers.parser.threads import ThreadsParser
from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.bilibili import BiliAPI
from parsehub.provider_api.threads import ThreadsAPI, ThreadsPost
from parsehub.provider_api.twitter import Twitter
from parsehub.utils.helpers import SecretCookie

EXPECTED_TWITTER_AT = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)


# ── twitter: legacy.created_at + views.count ──────────────────


def twitter_payload(created_at="Wed Oct 01 11:00:00 +0000 2026", views="24574"):
    result = {
        "rest_id": "1",
        "legacy": {
            "full_text": "hello",
            "entities": {"urls": [], "media": []},
            "created_at": created_at,
        },
        "core": {"user_results": {"result": {"legacy": {"name": "Me", "screen_name": "me"}}}},
    }
    if views is not None:
        result["views"] = {"count": views}
    return {"data": {"tweetResult": {"result": result}}}


def test_twitter_reads_published_at_and_views():
    tweet = Twitter().parse(twitter_payload())
    assert tweet.published_at == EXPECTED_TWITTER_AT
    assert tweet.view_count == 24574


def test_twitter_without_views_field():
    """部分响应不带 views 节点"""
    tweet = Twitter().parse(twitter_payload(views=None))
    assert tweet.published_at == EXPECTED_TWITTER_AT
    assert tweet.view_count is None


def test_twitter_parser_forwards_metadata():
    tweet = Twitter().parse(twitter_payload())
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.published_at == EXPECTED_TWITTER_AT
    assert result.view_count == 24574


# ── yt-dlp (facebook / youtube / snapchat / bilibili 兜底) ────


def yt_info(**extra):
    info = {
        "title": "T",
        "description": "D",
        "thumbnail": "https://cdn.example/t.jpg",
        "webpage_url": "https://www.facebook.com/watch/?v=1",
        "duration": 74,
        "width": 640,
        "height": 360,
        "timestamp": 1417766402,
        "view_count": 2823887,
    }
    info.update(extra)
    return YtVideoInfo(
        title=info["title"],
        description=info["description"],
        thumbnail=info["thumbnail"],
        url=info["webpage_url"],
        duration=info["duration"],
        width=info["width"],
        height=info["height"],
        info_json=info,
    )


def test_ytdlp_reads_timestamp_and_view_count():
    """facebook 实测: timestamp (unix 秒) + view_count, 无点赞/评论"""
    dl = yt_info()
    assert dl.published_at == datetime(2014, 12, 5, 8, 0, 2, tzinfo=UTC)
    assert dl.view_count == 2823887


def test_ytdlp_without_stats():
    dl = yt_info(timestamp=None, view_count=None)
    assert dl.published_at is None
    assert dl.view_count is None


def test_ytdlp_tolerates_missing_thumbnail_and_description():
    """facebook 的条目可能没有缩略图/简介: 用下标访问会直接 KeyError 让整条解析失败"""
    info = {
        "title": "T",
        "webpage_url": "https://www.facebook.com/watch?v=1",
        "timestamp": 1759406400,
        "view_count": 5,
    }
    parsed = YtVideoInfo(
        title=info["title"],
        description="",
        thumbnail="",
        url=info["webpage_url"],
        info_json=info,
    )
    assert parsed.thumbnail == ""
    assert parsed.published_at == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)
    assert parsed.view_count == 5


def test_ytdlp_release_timestamp_as_fallback():
    dl = yt_info(timestamp=None, release_timestamp=1759406400)
    assert dl.published_at == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)


def test_ytdlp_parse_result_carries_metadata():
    from parsehub.parsers.base.ytdlp import YtVideoParseResult

    result = YtVideoParseResult(dl=yt_info(), title="T")
    assert result.published_at == datetime(2014, 12, 5, 8, 0, 2, tzinfo=UTC)
    assert result.view_count == 2823887


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/watch/?v=10153231379946729",
        "https://www.facebook.com/watch?v=10153231379946729",
        "https://www.facebook.com/someuser/videos/123456789/",
        "https://www.facebook.com/share/v/abc123/",
        "https://www.facebook.com/reel/123456789",
    ],
)
def test_facebook_url_forms_are_matched(url):
    assert FacebookParse.match(url)


def test_facebook_keeps_v_parameter():
    """v 是定位视频的参数, 被清理掉就会退化成 /watch 让 yt-dlp 解析失败"""
    raw = asyncio.run(FacebookParse().get_raw_url("https://www.facebook.com/watch?v=10153231379946729"))
    assert "v=10153231379946729" in raw


# ── bilibili: data.View.pubdate + stat.view ───────────────────


def bili_payload():
    return {
        "data": {
            "View": {
                "title": "标题",
                "cid": 123,
                "duration": 60,
                "dimension": {"width": 1920, "height": 1080},
                "desc": "简介",
                "pic": "https://cdn.example/p.jpg",
                "pubdate": 1790887010,
                "owner": {"name": "UP主", "mid": 1},
                "stat": {"view": 1522364, "like": 73070, "reply": 2069, "share": 13623},
            }
        }
    }


def test_bilibili_parser_reads_pubdate_and_views():
    parser = BiliParse(cookie=SecretCookie({"SESSDATA": "x"}))
    with (
        patch.object(BiliAPI, "get_video_info", new=AsyncMock(return_value=bili_payload())),
        patch.object(BiliAPI, "get_buvid", new=AsyncMock(return_value=("b3", "b4"))),
        patch.object(
            BiliAPI,
            "get_video_playurl",
            new=AsyncMock(return_value={"data": {"durl": [{"url": "https://cdn.example/v.mp4"}]}}),
        ),
    ):
        result = asyncio.run(parser._do_parse("https://www.bilibili.com/video/BV1PHay6UEaS"))

    assert result.published_at == datetime.fromtimestamp(1790887010, tz=UTC)
    assert result.view_count == 1522364


def test_bilibili_video_info_passes_cookie():
    """view/detail 端点匿名会被风控, provider 必须把 cookie 传下去"""
    parser = BiliParse(cookie=SecretCookie({"SESSDATA": "secret"}))
    called: dict = {}

    async def fake_get_video_info(self, url, cookie=None):
        called["cookie"] = cookie
        return bili_payload()

    with (
        patch.object(BiliAPI, "get_video_info", new=fake_get_video_info),
        patch.object(BiliAPI, "get_buvid", new=AsyncMock(return_value=("b3", "b4"))),
        patch.object(
            BiliAPI,
            "get_video_playurl",
            new=AsyncMock(return_value={"data": {"durl": [{"url": "https://cdn.example/v.mp4"}]}}),
        ),
    ):
        asyncio.run(parser._do_parse("https://www.bilibili.com/video/BV1PHay6UEaS"))

    assert called["cookie"] == {"SESSDATA": "secret"}


# ── douyin: aweme_detail.create_time + statistics.play_count ──


def douyin_payload():
    return {
        "aweme_detail": {
            "desc": "描述",
            "create_time": 1790887010,
            "statistics": {"digg_count": 100, "comment_count": 5, "share_count": 2, "play_count": 98765},
            "author": {"nickname": "作者"},
            "video": {
                "play_addr": {"url_list": ["https://cdn.example/v.mp4"], "width": 1080, "height": 1920},
                "cover": {"url_list": ["https://cdn.example/c.jpg"]},
                "width": 1080,
                "height": 1920,
                "duration": 15000,
                "bit_rate": [
                    {
                        "play_addr": {
                            "url_list": ["https://cdn.example/v.mp4"],
                            "width": 1080,
                            "height": 1920,
                            "data_size": 100,
                        },
                        "bit_rate": 1000,
                    }
                ],
            },
        }
    }


def test_douyin_reads_create_time_and_play_count():
    result = DouyinApiResult.parse(douyin_payload())
    assert result.type is DouyinMediaType.VIDEO
    assert result.published_at == datetime.fromtimestamp(1790887010, tz=UTC)
    assert result.view_count == 98765


def test_douyin_without_statistics():
    payload = douyin_payload()
    payload["aweme_detail"].pop("statistics")
    result = DouyinApiResult.parse(payload)
    assert result.view_count is None
    assert result.published_at == datetime.fromtimestamp(1790887010, tz=UTC)


# ── threads: taken_at (无浏览量字段) ──────────────────────────


def test_threads_reads_taken_at():
    post = ThreadsPost.from_graphql(
        {
            "taken_at": 1782900353,
            "caption": {"text": "正文"},
            "user": {"username": "someone", "full_name": "Someone"},
            "media_type": None,
        }
    )
    assert post.published_at == datetime.fromtimestamp(1782900353, tz=UTC)
    # 该 GraphQL 响应里没有浏览量, 页面上的 views 走别的接口
    assert post.view_count is None


def test_threads_parser_forwards_published_at():
    post = ThreadsPost(content="正文", published_at=datetime.fromtimestamp(1782900353, tz=UTC), author_name="Someone")
    parser = ThreadsParser()
    with patch.object(ThreadsAPI, "parse", new=AsyncMock(return_value=post)):
        result = asyncio.run(parser._do_parse("https://www.threads.com/@someone/post/ABC"))
    assert result.published_at == datetime.fromtimestamp(1782900353, tz=UTC)
    assert result.view_count is None
