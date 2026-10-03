"""被引用/被回复推文的媒体要交给引用块渲染。

引用块可以放图/视频 (真机验证过: RichBlockBlockQuotation 内嵌 RichBlockPhoto /
RichBlockAnimation)。解析层把它们追加到 media 末尾并用两个计数标出归属,
渲染层据此放进对应的引用块。
"""

import asyncio

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import TwitterAni, TwitterPhoto, TwitterTweet, TwitterVideo


def _photo(url: str) -> TwitterPhoto:
    return TwitterPhoto(url=url, width=1200, height=800, thumb_url=url + ":thumb")


def _video(url: str) -> TwitterVideo:
    return TwitterVideo(url=url, width=1280, height=720, duration_millis=12000, thumb_url=url + ":thumb")


def _tweet(**kw) -> TwitterTweet:
    kw.setdefault("full_text", "正文")
    kw.setdefault("author_handle", "me")
    return TwitterTweet(tweet_id=kw.pop("tweet_id", "1"), **kw)


def test_quoted_media_appended_and_counted():
    tweet = _tweet(
        media=[_photo("https://pbs.twimg.com/body.jpg")],
        quoted_status_id="9",
        quoted_status=_tweet(tweet_id="9", full_text="引用", media=[_photo("https://pbs.twimg.com/quoted.jpg")]),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 2
    assert result.quoted_media_count == 1
    assert result.reply_media_count == 0
    assert result.media[-1].url == "https://pbs.twimg.com/quoted.jpg"


def test_reply_media_goes_between_body_and_quoted():
    """顺序必须是 [正文..., 被回复..., 被引用...] —— 渲染层靠它切两刀"""
    tweet = _tweet(
        media=[_photo("https://pbs.twimg.com/body.jpg")],
        reply_to=_tweet(tweet_id="8", full_text="回复", media=[_video("https://video.twimg.com/reply.mp4")]),
        quoted_status_id="9",
        quoted_status=_tweet(tweet_id="9", full_text="引用", media=[_photo("https://pbs.twimg.com/quoted.jpg")]),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    urls = [m.url for m in result.media]
    assert urls == [
        "https://pbs.twimg.com/body.jpg",
        "https://video.twimg.com/reply.mp4",
        "https://pbs.twimg.com/quoted.jpg",
    ]
    assert result.reply_media_count == 1
    assert result.quoted_media_count == 1


def test_reply_media_without_quote():
    tweet = _tweet(
        reply_to=_tweet(tweet_id="8", media=[_photo("https://pbs.twimg.com/reply.jpg")]),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 1
    assert result.reply_media_count == 1
    assert result.quoted_media_count == 0


def test_counts_are_zero_without_related_media():
    result = asyncio.run(TwitterParser.media_parse(_tweet(media=[_photo("https://pbs.twimg.com/body.jpg")])))
    assert len(result.media) == 1
    assert result.reply_media_count == 0
    assert result.quoted_media_count == 0


def test_quoted_without_media_changes_nothing():
    """被引用推文没有媒体时不该多出计数"""
    tweet = _tweet(quoted_status_id="9", quoted_status=_tweet(tweet_id="9", full_text="引用"))
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.media is None or len(result.media) == 0
    assert result.quoted_media_count == 0
    assert result.reply_media_count == 0


def test_animation_media_of_quoted_is_converted():
    anis = TwitterAni(url="https://video.twimg.com/q.mp4", width=600, height=600, thumb_url="t")
    tweet = _tweet(quoted_status_id="9", quoted_status=_tweet(tweet_id="9", media=[anis]))
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 1
    assert result.quoted_media_count == 1
