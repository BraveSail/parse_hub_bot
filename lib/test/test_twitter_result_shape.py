"""推文响应的两种形态都要解析对: `{tweet: {...}}` (包装) 与字段铺在顶层 (平铺)。

背景（2026-10-05）: 一条长推文 (note tweet) 只解析出前 147 字符 —— 正文尾部整段
消失, 而且作者名、作者链接、浏览量都是空的。原因是字段 (note_tweet / core / views /
article / quoted_status_result) 在**包装形态**下位于 `result["tweet"]` 里, 而代码固定
从顶层 `result` 取, 于是取不到 → 静默回落成 `legacy.full_text`(长文的预览片段)。

长文的完整正文只在 `note_tweet.note_tweet_results.result.text`, `legacy.full_text`
永远是截断的预览 —— 所以这里两种形态都要断言, 否则又会静默丢尾巴。
"""

from parsehub.provider_api.twitter import Twitter

PREVIEW = "SOS団合同お誕生日会、開催\n\nハルヒ、キョン\n"
FULL = PREVIEW + "\n最高の誕生日を祝っていただきました\n\nいつまでも、みんな大好き\n\n#涼宮ハルヒの憂鬱"


def _legacy(text: str = PREVIEW, **extra) -> dict:
    return {"full_text": text, "entities": {"urls": [], "media": []}, **extra}


def _note_tweet(text: str) -> dict:
    return {
        "is_expandable": True,
        "note_tweet_results": {"result": {"id": "1", "text": text, "entity_set": {"urls": []}}},
    }


def _core(name: str = "平野綾", handle: str = "Hysteric_Barbie") -> dict:
    return {"user_results": {"result": {"legacy": {"name": name, "screen_name": handle}}}}


def _wrapped(**tweet_fields) -> dict:
    """包装形态: 字段在 result["tweet"] 里 (实测长推文返回这种)"""
    tweet = {"rest_id": "1", "legacy": _legacy()}
    tweet.update(tweet_fields)
    return {"data": {"tweetResult": {"result": {"__typename": "Tweet", "tweet": tweet}}}}


def _flat(**result_fields) -> dict:
    """平铺形态: 字段直接在 result 上"""
    result = {"rest_id": "1", "legacy": _legacy()}
    result.update(result_fields)
    return {"data": {"tweetResult": {"result": result}}}


# ── 包装形态 (实测长推文走这条) ──────────────────────────────


def test_a_wrapped_long_tweet_keeps_its_whole_text():
    """包装形态 + note_tweet: 必须用完整正文, 不能回落到 legacy 的预览片段"""
    payload = _wrapped(note_tweet=_note_tweet(FULL), core=_core())
    tweet = Twitter().parse(payload)
    assert tweet.full_text == FULL, "长文正文被截断成预览"
    assert "#涼宮ハルヒの憂鬱" in tweet.full_text


def test_a_wrapped_tweet_keeps_author_and_views():
    """包装形态下作者名/标识/浏览量都要取到 (它们同样只在 tweet 里)"""
    payload = _wrapped(core=_core(), views={"count": "71706"})
    tweet = Twitter().parse(payload)
    assert tweet.author_name == "平野綾"
    assert tweet.author_handle == "Hysteric_Barbie"
    assert tweet.view_count == 71706  # TwitterTweet 会转成 int


def test_a_wrapped_tweet_without_note_tweet_falls_back_to_legacy():
    """没有长文字段时用 legacy.full_text (普通推文)"""
    payload = _wrapped(core=_core())
    tweet = Twitter().parse(payload)
    assert tweet.full_text == PREVIEW
    assert tweet.author_handle == "Hysteric_Barbie"


def test_a_wrapped_quote_is_found():
    """被引用推文的数据也在 tweet 层"""
    quoted = {"rest_id": "999", "legacy": _legacy("original"), "core": _core("Other", "other")}
    payload = _wrapped(core=_core(), quoted_status_result={"result": quoted})
    tweet = Twitter().parse(payload)
    assert tweet.quoted_status is not None, "包装形态下的被引用推文没被解析"
    assert tweet.quoted_status.full_text == "original"


# ── 平铺形态 (既有测试用的那种, 防回归) ──────────────────────


def test_the_flat_shape_still_parses():
    payload = _flat(core=_core(), views={"count": "100"})
    tweet = Twitter().parse(payload)
    assert tweet.author_name == "平野綾"
    assert tweet.author_handle == "Hysteric_Barbie"
    assert tweet.view_count == 100
    assert tweet.full_text == PREVIEW


def test_the_flat_shape_still_reads_a_note_tweet():
    payload = _flat(note_tweet=_note_tweet(FULL), core=_core())
    assert Twitter().parse(payload).full_text == FULL


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
