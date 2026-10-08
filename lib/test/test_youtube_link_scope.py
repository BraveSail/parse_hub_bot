"""YouTube 链接卡片：作用域是**被引用 / 被回复的推文**，且**只出文字行、不出图片**。

用户原话（两轮，后者纠正前者）:
  「之前让你修的是 引用里面的链接没有封面，我让你统一，作用域是引用或者回复」
  「有引用就引用吗，没引用就不弄，链接你放那里不管就行了」
  「我之前让弄的是**给视频加封面**，不是把封面再发一遍」

⇒ 卡片是**可点的标题行**（插在对应引用块内部）；封面**不是**独立媒体。
   给视频配封面是另一个机制（``VideoRef.thumb_url`` → ``prepare_video_thumbs``）。

所以:

- **被引用推文**（``quoted_status``）正文里的链接 → 引用块里加可点标题行，**不产出媒体**；
- **被回复推文**（``reply_to``）正文里的链接 → 同上；
- **主帖自己的正文**里的链接 → **一概不处理**，原样留着（不抓卡片、不套引用块）。

媒体顺序（渲染层从末尾往前切两个引用块）**只含各档自己的媒体**，卡片不占位::

    [正文..., 被回复自己的媒体..., 被引用自己的媒体...]
"""

import asyncio
from unittest.mock import patch

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import TwitterPhoto, TwitterTweet

YT = "https://youtube.com/shorts/hnSeq_P3jAo"
COVER = "https://i.ytimg.com/vi/x/hq2.jpg"


def _tweet(**kwargs) -> TwitterTweet:
    base = {
        "tweet_id": "1",
        "full_text": "正文",
        "author_name": "作者",
        "author_handle": "handle",
    }
    base.update(kwargs)
    return TwitterTweet(**base)


class _FakeCard:
    url = YT
    title = "标题"
    cover_url = COVER


def _parse(tweet: TwitterTweet, *, cover: bool = True, monkeypatch=None):
    async def fake_fetch_card(_url, **_kw):
        return _FakeCard() if cover else None

    with patch("parsehub.provider_api.youtube.fetch_card", new=fake_fetch_card):
        return asyncio.run(TwitterParser.media_parse(tweet))


# ---------------------------------------------------------------- 主帖正文: 不管


def test_a_link_in_the_main_body_is_left_alone():
    """**核心**: 主帖正文里的链接不加工 —— 不抓封面、不套引用块、不进任何引用档。"""
    result = _parse(_tweet(full_text=f"see {YT}"))
    assert len(result.media or []) == 0, "主帖正文的链接不该产出媒体"
    assert result.quoted_media_count == 0
    assert result.reply_media_count == 0
    assert "> <i><a" not in result.content, result.content
    assert YT in result.content, "链接本身照旧留在正文"


# ---------------------------------------------------------------- 被引用推文


def test_a_link_inside_the_quoted_post_gets_a_card_but_no_media():
    """**核心**: 被引用推文正文里的链接 → 引用块里加可点标题行，**不产出图片**。"""
    quoted = _tweet(tweet_id="2", full_text=f"看这个 {YT}")
    result = _parse(_tweet(quoted_status=quoted))

    assert result.quoted_media_count == 0, "卡片是文字行, 不占媒体档位"
    assert len(result.media or []) == 0, "封面不得作为独立媒体发出"
    assert "> <i><a" in result.content, result.content
    assert result.reply_media_count == 0


def test_the_quoted_card_sits_inside_the_quoted_block():
    """卡片要在**被引用引用块内部**（与它的正文同一块），不能在块外单独一段。

    ``format_quote_block`` 末尾自带一个空行作为块的结束 —— 卡片必须插在那个空行之前。
    """
    quoted = _tweet(tweet_id="2", full_text=f"看这个 {YT}")
    result = _parse(_tweet(quoted_status=quoted))
    lines = [ln for ln in result.content.splitlines() if ln.strip()]
    card_at = next(i for i, ln in enumerate(lines) if "<a href=" in ln and "youtube" in ln)
    # 卡片行必须以 `> ` 开头（在引用块里），且它上面一行也是引用块
    assert lines[card_at].startswith("> "), lines[card_at]
    assert lines[card_at - 1].startswith("> "), lines[card_at - 1]


def test_only_the_quoted_post_own_media_counts():
    """引用档只数**它自己的媒体** —— 卡片（文字行）不占档位。

    这正是用户报的「引用里怎么多一张图」：被引用帖自己的视频/图之外，
    那张 YouTube 封面不该再占一个媒体位。
    """
    quoted = _tweet(
        tweet_id="2",
        full_text=f"看这个 {YT}",
        media=[TwitterPhoto(url="https://pbs/x.jpg", height=1, width=1)],
    )
    result = _parse(_tweet(quoted_status=quoted))
    assert result.quoted_media_count == 1
    assert [type(m).__name__ for m in result.media] == ["ImageRef"]
    assert result.media[0].url == "https://pbs/x.jpg"
    assert COVER not in [m.url for m in result.media]


# ---------------------------------------------------------------- 被回复推文


def test_a_link_inside_the_replied_post_gets_a_card_but_no_media():
    """被回复推文正文里的链接 → 回复块里加可点标题行，**不产出图片**"""
    reply = _tweet(tweet_id="3", full_text=f"看这个 {YT}")
    result = _parse(_tweet(reply_to=reply))

    assert result.reply_media_count == 0
    assert result.quoted_media_count == 0
    assert len(result.media or []) == 0
    assert "> <i><a" in result.content


def test_reply_and_quoted_cards_both_render_without_media():
    """两处链接各有自己的卡片行, 但都不产出媒体（不占任何档位）"""
    reply = _tweet(tweet_id="3", full_text=f"回复里的 {YT}")
    quoted = _tweet(tweet_id="4", full_text=f"引用里的 {YT}")
    result = _parse(_tweet(reply_to=reply, quoted_status=quoted))

    assert result.reply_media_count == 0
    assert result.quoted_media_count == 0
    assert len(result.media or []) == 0
    assert result.content.count("> <i><a href=") >= 2, "两处卡片行都要在"


def test_the_reply_card_comes_before_the_body_and_the_quoted_block_after():
    """位置与 X 一致: 被回复在最前、被引用在最后"""
    reply = _tweet(tweet_id="3", full_text="被回复的文字")
    quoted = _tweet(tweet_id="4", full_text=f"引用里的 {YT}")
    result = _parse(_tweet(full_text="主帖正文", reply_to=reply, quoted_status=quoted))

    text = result.content
    assert text.index("被回复的文字") < text.index("主帖正文") < text.index("引用里的")


# ---------------------------------------------------------------- 抓不到封面


def test_a_quoted_link_with_no_card_stays_plain():
    """抓不到卡片信息时不加卡片行（卡片是锦上添花，不该让解析失败）"""
    quoted = _tweet(tweet_id="2", full_text=f"看这个 {YT}")
    result = _parse(_tweet(quoted_status=quoted), cover=False)
    assert result.quoted_media_count == 0
    assert len(result.media or []) == 0
    assert YT in result.content


def test_a_quoted_post_without_any_link_is_unchanged():
    quoted = _tweet(tweet_id="2", full_text="纯文字引用")
    result = _parse(_tweet(quoted_status=quoted))
    assert result.quoted_media_count == 0
    assert "纯文字引用" in result.content


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
