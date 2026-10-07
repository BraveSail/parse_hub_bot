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


def test_a_media_only_quoted_tweet_keeps_its_media_in_the_card():
    """**核心**: 引用帖正文为空、只有图（用户报的那条）—— 图必须算进引用块。

    实测 ``x.com/sim_dr1/status/2107780697251574187``：主帖正文只有 ``＃ステラソラ``、
    1 张图；被引用帖**正文是空串**（原文只有一个媒体短链）、1 张图。
    引用块为空时 ``quoted_media_count`` 是 0 ⇒ 引用帖的图被当成正文图，
    两张图混进同一个图集（用户报「正文图为什么和引用图塞一起」）。
    """
    tweet = _tweet(
        full_text="＃ステラソラ",
        media=[_photo("https://pbs.twimg.com/body.jpg")],
        quoted_status_id="2107780606574903574",
        quoted_status=_tweet(
            tweet_id="2107780606574903574",
            full_text="",  # 正文为空 —— 原文只有一个媒体短链
            author_handle="SIM_DR1",
            author_name="しむ",
            media=[_photo("https://pbs.twimg.com/quoted.jpg")],
        ),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 2
    assert result.quoted_media_count == 1, "引用帖的图必须归引用块"
    assert result.reply_media_count == 0
    assert result.media[-1].url == "https://pbs.twimg.com/quoted.jpg"

    # 引用块本体在正文之后，只有署名行 + 换行（图由计数通道放进块内）
    md = result.content
    assert md.startswith("＃ステラソラ"), md
    tail = [ln for ln in md.splitlines() if ln.startswith("> ")]
    assert tail == ['> <i><a href="https://x.com/SIM_DR1">しむ</a> <code>@SIM_DR1</code></i>'], tail


def test_a_media_only_reply_keeps_its_media_in_the_card():
    """回复侧的同一个坑（被回复的纯图推文）"""
    tweet = _tweet(
        media=[_photo("https://pbs.twimg.com/body.jpg")],
        reply_to=_tweet(tweet_id="8", full_text="", media=[_photo("https://pbs.twimg.com/reply.jpg")]),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 2
    assert result.reply_media_count == 1, "被回复帖的图必须归回复卡片"


def test_a_quoted_tweet_with_neither_text_nor_media_adds_nothing():
    """既没文字也没媒体的引用帖 —— 不该凭空多出一个空引用块与计数"""
    tweet = _tweet(
        media=[_photo("https://pbs.twimg.com/body.jpg")],
        quoted_status_id="9",
        quoted_status=_tweet(tweet_id="9", full_text=""),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert len(result.media) == 1
    assert result.quoted_media_count == 0
    assert not [ln for ln in result.content.splitlines() if ln.startswith("> ")]


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
