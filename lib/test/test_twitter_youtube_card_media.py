"""正文里 YouTube 链接的封面属于**正文**，不该被算进引用媒体。

用户报「链接的预览放引用里了」。

取证（`https://x.com/ikizulive_staff/status/2107425646582374748`，真实响应）::

    主帖: 1 个视频 + 正文里一条 YouTube Shorts 链接
    tweet.quoted_status = False      ← **没有引用推文**
    tweet.reply_to      = False      ← 也不是回复
    我们产出: media = [视频, YouTube封面] 且 quoted_media_count = 1   ← 把封面算成"引用媒体"

根因：``quoted_total = len(quoted_media) + len(yt_media)`` —— YouTube 封面无条件进
``quoted_media_count``。那个计数是给"真有一条被引用的推文"用的，渲染层会按它把媒体
切进**引用卡片**；而 YouTube 卡片的文字恰好也是引用块形态（``> <i>…</i>``），
于是**没有引用/回复的推文**，封面被吸进了那个卡片。

⇒ 修法：``quoted_total`` 只数**被引用推文**的媒体；YouTube 封面留在 media 末尾但归正文。
``reply_media_count`` 同理只数"真渲染出引用块"的那些。
"""

import asyncio
from unittest.mock import patch

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import TwitterAni, TwitterPhoto, TwitterTweet, TwitterVideo
from parsehub.types import ImageRef

YT_URL = "https://youtube.com/shorts/hnSeq_P3jAo"


def _tweet(**kwargs) -> TwitterTweet:
    base = {
        "tweet_id": "1",
        "full_text": "推文正文",
        "author_name": "作者",
        "author_handle": "handle",
    }
    base.update(kwargs)
    return TwitterTweet(**base)


async def _no_card(_text=None):
    """没有 YouTube 卡片的场景"""
    return "", []


async def _with_yt_card(_text=None):
    """正文里的 YouTube 链接成功抓到卡片 + 封面"""
    return '> <i><a href="https://youtube.com/shorts/x">标题</a></i>', [
        ImageRef(url="https://i.ytimg.com/vi/x/hq2.jpg")
    ]


def _parse(tweet: TwitterTweet, card):
    with patch.object(TwitterParser, "_youtube_card", card):
        return asyncio.run(TwitterParser.media_parse(tweet))


# ---------------------------------------------------------------- 核心回归


def test_a_youtube_cover_is_not_counted_as_quoted_media():
    """**核心**: 没有引用推文时，YouTube 封面不该进 ``quoted_media_count``。

    进了就会被渲染层切进"引用卡片" —— 用户看到的就是「链接的预览放引用里了」。
    """
    result = _parse(_tweet(), _with_yt_card)
    assert result.quoted_media_count == 0, result.quoted_media_count
    assert len(result.media) == 1, "封面本身仍要发（只是不算引用档）"
    assert isinstance(result.media[0], ImageRef)


def test_a_youtube_cover_is_not_counted_as_reply_media_either():
    """同理不进 ``reply_media_count``（那档是回复卡片的）"""
    result = _parse(_tweet(), _with_yt_card)
    assert result.reply_media_count == 0


# ---------------------------------------------------------------- 别把它改坏


def test_a_real_quoted_tweet_still_counts():
    """真引用推文的媒体**照旧**进 ``quoted_media_count``（这次改动不能把它弄丢）"""
    quoted = _tweet(tweet_id="2", full_text="被引用", media=[TwitterPhoto(url="https://pbs/x.jpg", height=1, width=1)])
    result = _parse(_tweet(quoted_status=quoted), _no_card)
    assert result.quoted_media_count == 1


def test_quoted_media_plus_a_youtube_cover_count_only_the_quote():
    """两者都有时: 只有**被引用推文的**媒体算引用档，YouTube 封面不算"""
    quoted = _tweet(tweet_id="2", full_text="被引用", media=[TwitterPhoto(url="https://pbs/x.jpg", height=1, width=1)])
    result = _parse(_tweet(quoted_status=quoted), _with_yt_card)
    assert result.quoted_media_count == 1, result.quoted_media_count
    assert len(result.media) == 2


def test_a_reply_still_counts_its_media():
    """真回复的媒体进 ``reply_media_count``"""
    reply = _tweet(tweet_id="3", full_text="被回复", media=[TwitterPhoto(url="https://pbs/r.jpg", height=1, width=1)])
    result = _parse(_tweet(reply_to=reply), _no_card)
    assert result.reply_media_count == 1


def test_the_post_own_media_stays_in_the_body():
    """主帖自己的媒体（视频/图）不受影响 —— 一直在正文档"""
    own = [
        TwitterVideo(
            url="https://video.twimg/v.mp4", thumb_url="https://pbs/t.jpg", width=1, height=1, duration_millis=1000
        )
    ]
    result = _parse(_tweet(media=own), _with_yt_card)
    assert len(result.media) == 2  # 视频 + YouTube 封面
    assert result.quoted_media_count == 0
    assert result.reply_media_count == 0


def test_an_animated_gif_also_stays_in_the_body():
    own = [TwitterAni(url="https://video.twimg/a.mp4", thumb_url="https://pbs/t.jpg", width=1, height=1)]
    result = _parse(_tweet(media=own), _no_card)
    assert result.quoted_media_count == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
