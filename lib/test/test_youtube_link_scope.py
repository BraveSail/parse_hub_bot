"""YouTube 链接封面：作用域是**被引用 / 被回复的推文**，不是主帖正文。

用户原话:
  「之前让你修的是 引用里面的链接没有封面，我让你统一，作用域是引用或者回复」
  「有引用就引用吗，没引用就不弄，链接你放那里不管就行了」

所以:

- **被引用推文**（``quoted_status``）正文里的 YouTube 链接 → 引用卡片 + 封面，封面归
  ``quoted_media_count`` 那一档（它会渲染进被引用卡片里）；
- **被回复推文**（``reply_to``）正文里的链接 → 同上，归 ``reply_media_count`` 那一档；
- **主帖自己的正文**里的链接 → **一概不处理**，原样留着（不抓封面、不套引用块）。

媒体顺序（渲染层从末尾往前切两个引用块）::

    [正文..., 被回复(含其卡片封面)..., 被引用(含其卡片封面)...]
"""

import asyncio
from unittest.mock import patch

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import TwitterPhoto, TwitterTweet
from parsehub.types import ImageRef

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
    assert "> <a" not in result.content, result.content
    assert YT in result.content, "链接本身照旧留在正文"


# ---------------------------------------------------------------- 被引用推文


def test_a_link_inside_the_quoted_post_gets_a_card_and_cover():
    """**核心**: 被引用推文正文里的链接 → 卡片进引用块 + 封面归引用档。"""
    quoted = _tweet(tweet_id="2", full_text=f"看这个 {YT}")
    result = _parse(_tweet(quoted_status=quoted))

    assert result.quoted_media_count == 1, "封面要归引用档（渲染进被引用卡片里）"
    assert len(result.media or []) == 1
    assert isinstance(result.media[0], ImageRef)
    assert result.media[0].url == COVER
    assert "> <a" in result.content, result.content
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


def test_the_quoted_post_own_media_and_the_cover_both_count():
    """被引用帖自己的媒体 + 它的卡片封面都归引用档（顺序: 媒体在前、封面在后）"""
    quoted = _tweet(
        tweet_id="2",
        full_text=f"看这个 {YT}",
        media=[TwitterPhoto(url="https://pbs/x.jpg", height=1, width=1)],
    )
    result = _parse(_tweet(quoted_status=quoted))
    assert result.quoted_media_count == 2
    assert [type(m).__name__ for m in result.media] == ["ImageRef", "ImageRef"]
    assert result.media[0].url == "https://pbs/x.jpg"
    assert result.media[1].url == COVER


# ---------------------------------------------------------------- 被回复推文


def test_a_link_inside_the_replied_post_gets_a_card_and_cover():
    """被回复推文正文里的链接 → 卡片进回复块 + 封面归回复档"""
    reply = _tweet(tweet_id="3", full_text=f"看这个 {YT}")
    result = _parse(_tweet(reply_to=reply))

    assert result.reply_media_count == 1
    assert result.quoted_media_count == 0
    assert result.media[0].url == COVER
    assert "> <a" in result.content


def test_reply_and_quoted_links_land_in_their_own_tiers():
    """两种都有时各归各的档 —— 顺序是 [正文..., 回复(含封面)..., 引用(含封面)...]"""
    reply = _tweet(tweet_id="3", full_text=f"回复里的 {YT}")
    quoted = _tweet(tweet_id="4", full_text=f"引用里的 {YT}")
    result = _parse(_tweet(reply_to=reply, quoted_status=quoted))

    assert result.reply_media_count == 1
    assert result.quoted_media_count == 1
    assert len(result.media) == 2


def test_the_reply_card_comes_before_the_body_and_the_quoted_block_after():
    """位置与 X 一致: 被回复在最前、被引用在最后"""
    reply = _tweet(tweet_id="3", full_text="被回复的文字")
    quoted = _tweet(tweet_id="4", full_text=f"引用里的 {YT}")
    result = _parse(_tweet(full_text="主帖正文", reply_to=reply, quoted_status=quoted))

    text = result.content
    assert text.index("被回复的文字") < text.index("主帖正文") < text.index("引用里的")


# ---------------------------------------------------------------- 抓不到封面


def test_a_quoted_link_with_no_cover_stays_plain():
    """抓不到封面时不加卡片、不加媒体（封面是锦上添花，不该让解析失败）"""
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
