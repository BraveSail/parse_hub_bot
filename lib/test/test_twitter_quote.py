"""推文引用 (quote) 时, 应把被引用推文渲染成引用块 (markdown 引用)."""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import Twitter, TwitterTweet


def make_result(text, rest_id="1", handle="me", name="Me", views=None, **legacy_extra):
    legacy = {"full_text": text, "entities": {"urls": [], "media": []}, **legacy_extra}
    result = {
        "rest_id": rest_id,
        "legacy": legacy,
        "core": {"user_results": {"result": {"legacy": {"name": name, "screen_name": handle}}}},
    }
    if views is not None:
        result["views"] = {"count": str(views)}
    return result


def make_payload(result=None, quoted=None):
    if result is None:
        result = make_result("mine")
    if quoted is not None:
        result["quoted_status_result"] = {"result": quoted}
        result["legacy"]["is_quote_status"] = True
        result["legacy"]["quoted_status_id_str"] = quoted.get("rest_id") or "999"
    return {"data": {"tweetResult": {"result": result}}}


def quoted_tweet(text="original", handle="other", name="Other"):
    return TwitterTweet(tweet_id="999", full_text=text, author_handle=handle, author_name=name)


# ── provider: 内嵌引用数据的解析 ──────────────────────────────


def test_parse_reads_quoted_status():
    """被引用推文的完整数据就在同一份响应里, 不需要二次请求"""
    quoted = make_result("original", rest_id="999", handle="other", name="Other")
    tweet = Twitter().parse(make_payload(quoted=quoted))
    assert tweet.quoted_status is not None
    assert tweet.quoted_status.full_text == "original"
    assert tweet.quoted_status.author_handle == "other"
    assert tweet.quoted_status.author_name == "Other"


def test_parse_without_quote_has_none():
    assert Twitter().parse(make_payload()).quoted_status is None


def test_parse_skips_tombstone_quote():
    """被引用推文被限制/删除时只跳过引用, 不能拖垮主推文"""
    tombstone = {"__typename": "TweetTombstone", "rest_id": "999"}
    tweet = Twitter().parse(make_payload(quoted=tombstone))
    assert tweet.quoted_status is None
    assert tweet.full_text == "mine"


def test_parse_skips_quote_without_result():
    payload = make_payload()
    payload["data"]["tweetResult"]["result"]["quoted_status_result"] = {}
    assert Twitter().parse(payload).quoted_status is None


def test_parse_keeps_reply_and_quote_separate():
    """回复与引用互不影响: in_reply_to 与 quoted 可同时存在"""
    quoted = make_result("original", rest_id="999", handle="other")
    payload = make_payload(make_result("mine", "1", in_reply_to_status_id_str="555"), quoted=quoted)
    tweet = Twitter().parse(payload)
    assert tweet.reply_to_id == "555"
    assert tweet.quoted_status is not None
    assert tweet.quoted_status.full_text == "original"


def test_quote_media_does_not_leak_into_main_tweet():
    """被引用推文的媒体属于它自己, 不能变成主推文的媒体"""
    quoted = make_result("orig", rest_id="999")
    quoted["legacy"]["entities"]["media"] = [
        {
            "type": "photo",
            "media_url_https": "https://pbs.twimg.com/media/x.jpg",
            "original_info": {"width": 10, "height": 10},
        }
    ]
    tweet = Twitter().parse(make_payload(quoted=quoted))
    assert tweet.media is None
    assert tweet.quoted_status.media is not None


def test_parse_restores_short_urls_inside_quote():
    """被引用推文正文里的 t.co 也要按 entities 还原"""
    quoted = make_result("see https://t.co/abc", rest_id="999")
    quoted["legacy"]["entities"]["urls"] = [{"url": "https://t.co/abc", "expanded_url": "https://example.com/x"}]
    tweet = Twitter().parse(make_payload(quoted=quoted))
    assert tweet.quoted_status.full_text == "see https://example.com/x"


def test_parse_reads_quoted_is_sensitive():
    quoted = make_result("orig", rest_id="999", possibly_sensitive=True)
    tweet = Twitter().parse(make_payload(quoted=quoted))
    assert tweet.quoted_status.is_sensitive is True


# ── provider: 统计字段 ───────────────────────────────────────


def test_parse_reads_like_and_view_counts():
    """点赞数取 legacy.favorite_count, 浏览量取顶层 views.count"""
    payload = make_payload(make_result("hi", favorite_count=208, views=25009))
    tweet = Twitter().parse(payload)
    assert tweet.like_count == 208
    assert tweet.view_count == 25009


def test_parse_without_counts_leaves_none():
    """平台没给这两项时留 None (展示层整段跳过)"""
    payload = make_payload(make_result("hi"))
    tweet = Twitter().parse(payload)
    assert tweet.like_count is None


def test_media_parse_forwards_like_count():
    tweet = TwitterTweet(tweet_id="1", full_text="x", like_count=1351)
    assert asyncio.run(TwitterParser.media_parse(tweet)).like_count == 1351


def test_quoted_like_count_is_independent_of_the_main_tweet():
    """引用块里的被引用推文有自己的点赞数, 不能串到主推"""
    quoted = make_result("orig", rest_id="999", favorite_count=5)
    payload = make_payload(make_result("mine", favorite_count=100), quoted=quoted)
    tweet = Twitter().parse(payload)
    assert tweet.like_count == 100
    assert tweet.quoted_status.like_count == 5


# ── parser: 引用块渲染 ────────────────────────────────────────


def test_quoted_block_renders_markdown():
    tweet = TwitterTweet(tweet_id="1", full_text="mine", quoted_status=quoted_tweet("line1\nline2"))
    assert TwitterParser._build_quoted_block(tweet) == (
        '> <i><a href="https://x.com/other">@other</a></i>\n> <i>line1</i>\n> <i>line2</i>\n\n'
    )


def test_quoted_block_marks_blank_lines():
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet("a\n\nb"))
    assert TwitterParser._build_quoted_block(tweet) == (
        '> <i><a href="https://x.com/other">@other</a></i>\n> <i>a</i>\n>\n> <i>b</i>\n\n'
    )


def test_quoted_block_falls_back_to_author_name():
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet("x", handle="", name="夏吉ゆうこ"))
    assert TwitterParser._build_quoted_block(tweet) == "> <i>夏吉ゆうこ</i>\n> <i>x</i>\n\n"


def test_quoted_block_uses_handle_only_when_name_matches():
    """显示名与用户名相同时只写 @用户名, 避免 "same @same" 这种重复"""
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet("x", handle="same", name="same"))
    assert TwitterParser._build_quoted_block(tweet) == (
        '> <i><a href="https://x.com/same">@same</a></i>\n> <i>x</i>\n\n'
    )


def test_quoted_block_shows_name_and_handle():
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet("x", handle="huacnlee", name="Jason Lee"))
    assert TwitterParser._build_quoted_block(tweet) == (
        '> <i><a href="https://x.com/huacnlee">Jason Lee</a> <sub>@huacnlee</sub></i>\n> <i>x</i>\n\n'
    )


def test_quoted_block_without_author():
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet("x", handle="", name=""))
    assert TwitterParser._build_quoted_block(tweet) == "> <i>x</i>\n\n"


def test_quoted_block_skipped_without_quote():
    assert TwitterParser._build_quoted_block(TwitterTweet(tweet_id="1", full_text="mine")) == ""


def test_quoted_block_skipped_when_text_empty():
    tweet = TwitterTweet(tweet_id="1", quoted_status=quoted_tweet(""))
    assert TwitterParser._build_quoted_block(tweet) == ""


def test_media_parse_appends_quote_after_text():
    tweet = TwitterTweet(tweet_id="1", full_text="mine", quoted_status=quoted_tweet("original"))
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.content == 'mine\n\n> <i><a href="https://x.com/other">@other</a></i>\n> <i>original</i>'


def test_media_parse_keeps_reply_before_and_quote_after():
    tweet = TwitterTweet(
        tweet_id="1",
        full_text="mine",
        reply_to=TwitterTweet(tweet_id="2", full_text="parent", author_name="Parent", author_handle="parent"),
        quoted_status=quoted_tweet("original"),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    # "Parent"/"parent" 与 "Other"/"other" 忽略大小写视为同一名字, 只出 @用户名
    assert result.content == (
        '> <i><a href="https://x.com/parent">@parent</a></i>\n> <i>parent</i>\n\nmine\n\n'
        '> <i><a href="https://x.com/other">@other</a></i>\n> <i>original</i>'
    )


def test_media_parse_without_quote_is_unchanged():
    result = asyncio.run(TwitterParser.media_parse(TwitterTweet(tweet_id="1", full_text="mine")))
    assert result.content == "mine"


def test_rich_text_parse_appends_quote():
    from parsehub.provider_api.twitter import TwitterArticle

    tweet = TwitterTweet(
        tweet_id="1",
        article=TwitterArticle(title="T", content="# Body"),
        quoted_status=quoted_tweet("original"),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.markdown_content == '# Body\n\n> <i><a href="https://x.com/other">@other</a></i>\n> <i>original</i>'


def test_quote_end_to_end_from_payload():
    """从响应结构一路解析到渲染结果"""
    quoted = make_result("Vibe coding 的时候…", rest_id="999", handle="huacnlee", name="Jason Lee")
    payload = make_payload(make_result("往代码仓库里拉屎的就这些人"), quoted=quoted)
    result = asyncio.run(TwitterParser.media_parse(Twitter().parse(payload)))
    assert result.content == (
        "往代码仓库里拉屎的就这些人\n\n"
        '> <i><a href="https://x.com/huacnlee">Jason Lee</a>'
        ' <sub>@huacnlee</sub></i>\n> <i>Vibe coding 的时候…</i>'
    )


# ── provider: 内嵌数据被降级时按 ID 补取 ─────────────────────


def test_parse_records_quoted_status_id():
    """内嵌数据不可用时也要记下被引用推文的 ID, 供上层补取"""
    unavailable = {"__typename": "TweetUnavailable"}
    tweet = Twitter().parse(make_payload(quoted=unavailable))
    assert tweet.quoted_status is None
    assert tweet.quoted_status_id == "999"


def test_fetch_tweet_backfills_unavailable_quote():
    """X 常把内嵌引用降级成 TweetUnavailable, 此时要按 ID 再取一次"""
    main = make_payload(quoted={"__typename": "TweetUnavailable"})
    original = make_payload(make_result("original", rest_id="999", handle="other"), None)
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, original])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.quoted_status is not None
    assert tweet.quoted_status.full_text == "original"
    assert tweet.quoted_status.author_handle == "other"


def test_fetch_tweet_does_not_refetch_available_quote():
    """内嵌数据齐全时不应多发请求"""
    payload = make_payload(quoted=make_result("original", rest_id="999"))
    fetch = AsyncMock(return_value=payload)
    with patch.object(Twitter, "_fetch_result", new=fetch):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.quoted_status is not None
    assert fetch.await_count == 1


def test_fetch_tweet_ignores_quote_fetch_failure():
    """补取失败 (被引用推文已删/受限) 不能拖垮主推文"""
    main = make_payload(quoted={"__typename": "TweetUnavailable"})
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, RuntimeError("boom")])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.full_text == "mine"
    assert tweet.quoted_status is None


def test_fetch_tweet_backfills_quote_alongside_reply():
    """同时是回复和引用时, 两条关联推文各补一次"""
    main = make_payload(
        make_result("mine", "1", in_reply_to_status_id_str="555"),
        quoted={"__typename": "TweetUnavailable"},
    )
    parent = make_payload(make_result("parent", rest_id="555", handle="parent"))
    original = make_payload(make_result("original", rest_id="999", handle="other"))
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, parent, original])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.reply_to.full_text == "parent"
    assert tweet.quoted_status.full_text == "original"
