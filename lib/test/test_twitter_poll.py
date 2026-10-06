"""twitter 投票（poll）: 解析 + 渲染。

用户报：`https://twitter.com/thsottiaux/status/2107576143285219799` 的投票没处理。

**取证**（真实响应，见 ``fixtures/twitter_poll_card.json``）::

    tweet.full_text = "Vote"            <- 正文只有作者写的这一个词
    node["card"].legacy.name = "poll2choice_text_only"
    binding_values:
      choice1_label = "👌(good day)"        choice1_count = "11805"
      choice2_label = "🫨 (needs a reset)"  choice2_count = "34875"
      end_datetime_utc = "2026-10-07T00:58:03Z"
      counts_are_final = {boolean_value: false}

**关键结构**：投票**不在** ``legacy`` 上（``legacy`` 里没有任何 poll 字段），只在
``node["card"]`` 里 —— 与媒体、标签那两条路都不同，所以以前整条丢掉。

**选项数不固定**：卡片名形如 ``poll{2,3,4}choice_{text_only,image}``，键是
``choice{N}_label`` / ``choice{N}_count`` ⇒ 按 N 递增取，别硬编码 2 个。

**形态**：与 linux.do 的投票一致（选项 / 票数 / 占比三列）—— 表格由渲染层转成
服务端的 Table 块。
"""

import asyncio
import json
from pathlib import Path

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import Twitter, TwitterPoll, TwitterTweet

FIXTURE = Path(__file__).parent / "fixtures" / "twitter_poll_card.json"


def _real_tweet() -> TwitterTweet:
    return Twitter().parse(json.loads(FIXTURE.read_text()))


def _tweet(poll=None, **kwargs) -> TwitterTweet:
    base = {"tweet_id": "1", "full_text": "Vote", "author_name": "作者", "author_handle": "handle"}
    base.update(kwargs)
    return TwitterTweet(**base) if poll is None else TwitterTweet(poll=poll, **base)


def _poll(choices, **kwargs) -> TwitterPoll:
    return TwitterPoll(choices=choices, **kwargs)


def _render(tweet: TwitterTweet) -> str:
    return asyncio.run(TwitterParser.media_parse(tweet)).content


# ---------------------------------------------------------------- 解析（真实响应）


def test_the_choices_and_counts_come_from_the_card():
    """**核心**: 选项与票数来自卡片, 与响应原文逐项一致。"""
    poll = _real_tweet().poll
    assert poll is not None, "投票整条丢失了"
    assert poll.choices == [("👌(good day)", 11805), ("🫨 (needs a reset)", 34875)]


def test_the_end_time_and_final_flag_are_parsed():
    poll = _real_tweet().poll
    assert poll.end_datetime is not None and poll.end_datetime.year == 2026
    assert poll.is_final is False, "counts_are_final=false, 投票还没结束"


def test_a_tweet_without_a_card_has_no_poll():
    assert _tweet().poll is None


def test_the_option_count_is_not_hardcoded():
    """选项数不固定 —— 3 个、4 个都要取全（卡片名 poll3choice_* / poll4choice_*）"""
    for count in (2, 3, 4):
        card = {
            "legacy": {
                "name": f"poll{count}choice_text_only",
                "binding_values": [
                    *(
                        {"key": f"choice{i}_label", "value": {"string_value": f"选项{i}"}}
                        for i in range(1, count + 1)
                    ),
                    *({"key": f"choice{i}_count", "value": {"string_value": str(i * 10)}} for i in range(1, count + 1)),
                ],
            }
        }
        poll = Twitter._parse_poll({"card": card})
        assert len(poll.choices) == count, count
        assert poll.choices[-1] == (f"选项{count}", count * 10)


def test_a_missing_count_is_zero_rather_than_dropping_the_option():
    """票数缺失按 0 算 —— 选项本身仍要显示, 不该整条投票丢掉"""
    card = {
        "legacy": {
            "name": "poll2choice_text_only",
            "binding_values": [{"key": "choice1_label", "value": {"string_value": "A"}}],
        }
    }
    poll = Twitter._parse_poll({"card": card})
    assert poll.choices == [("A", 0)]


def test_a_card_that_is_not_a_poll_produces_nothing():
    """别的卡片（外链预览等）不该被当成投票"""
    assert Twitter._parse_poll({"card": {"legacy": {"name": "summary_large_image"}}}) is None
    assert Twitter._parse_poll({}) is None


def test_a_poll_card_yields_no_cover_photo():
    """投票卡片的图是选项配图, 不是"外链预览图" —— 不该走封面那条路"""
    node = json.loads(FIXTURE.read_text())
    node = (node.get("data") or {}).get("tweetResult", {}).get("result", {})
    node = node.get("tweet") or node
    assert Twitter._parse_card_photo(node) is None


# ---------------------------------------------------------------- 渲染


def test_the_poll_renders_as_a_table():
    """**核心**: 渲染成三列表格（与 linux.do 的投票同一形态）"""
    text = _render(_tweet(poll=_real_tweet().poll))
    assert "| 选项 | 票数 | 占比 |" in text, text
    assert "| --- | --- | --- |" in text, text
    assert "| 👌(good day) | 11805 | 25% |" in text, text
    assert "| 🫨 (needs a reset) | 34875 | 75% |" in text, text


def test_the_percentage_is_of_the_total():
    """占比按**总票数**算（11805/46680=25%, 34875/46680=75%）"""
    text = _render(_tweet(poll=_poll([("少", 1), ("多", 3)])))
    assert "| 少 | 1 | 25% |" in text, text
    assert "| 多 | 3 | 75% |" in text, text


def test_the_body_is_kept_above_the_table():
    """作者写的正文照旧保留在表格上方"""
    text = _render(_tweet(poll=_poll([("A", 1)])))
    assert text.startswith("Vote"), text
    assert text.index("Vote") < text.index("| 选项 |")


def test_a_poll_with_no_votes_does_not_blow_up():
    """一票都没有时不抛错, 占比记 0%"""
    text = _render(_tweet(poll=_poll([("A", 0), ("B", 0)])))
    assert "| A | 0 | 0% |" in text, text


def test_a_pipe_in_an_option_does_not_break_the_table():
    """选项文案里的 ``|`` 要转义 —— 否则会把列切断"""
    text = _render(_tweet(poll=_poll([("a|b", 1)])))
    assert r"| a\|b | 1 | 100% |" in text, text


def test_a_tweet_without_a_poll_has_no_table():
    text = _render(_tweet())
    assert "| 选项 |" not in text
    assert text.strip() == "Vote"


def test_an_empty_choices_list_renders_nothing():
    assert TwitterParser._build_poll(_tweet(poll=_poll([]))) == ""


# ---------------------------------------------------------------- 引用/回复里的投票


def test_a_poll_inside_the_quoted_post_renders_in_its_own_block():
    """被引用推文带投票时, 表格渲染在**它自己的引用块里**（与 X 上一致）"""
    quoted = _tweet(tweet_id="2", full_text="引用里的投票", poll=_poll([("A", 3), ("B", 1)]))
    text = _render(_tweet(full_text="主帖", quoted_status=quoted))
    lines = text.splitlines()
    table_at = next(i for i, ln in enumerate(lines) if "| 选项 |" in ln)
    assert all(ln.startswith("> ") for ln in lines[table_at : table_at + 4]), lines[table_at : table_at + 4]


def test_a_poll_inside_the_replied_post_renders_in_its_own_block():
    reply = _tweet(tweet_id="3", full_text="回复里的投票", poll=_poll([("A", 1)]))
    text = _render(_tweet(full_text="主帖", reply_to=reply))
    lines = text.splitlines()
    table_at = next(i for i, ln in enumerate(lines) if "| 选项 |" in ln)
    assert lines[table_at].startswith("> "), lines[table_at]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
