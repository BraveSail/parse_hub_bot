"""推文是回复时, 应把被回复推文渲染成引用块 (markdown 引用)."""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.parsers.parser.twitter import TwitterParser
from parsehub.provider_api.twitter import Twitter, TwitterTweet


def make_payload(text, reply_to_id=None, handle="me", name="Me"):
    legacy = {
        "full_text": text,
        "entities": {"urls": [], "media": []},
        "in_reply_to_status_id_str": reply_to_id,
    }
    return {
        "data": {
            "tweetResult": {
                "result": {
                    "rest_id": "1",
                    "legacy": legacy,
                    "core": {"user_results": {"result": {"legacy": {"name": name, "screen_name": handle}}}},
                }
            }
        }
    }


def reply_tweet(text="original", handle="other", name="Other"):
    return TwitterTweet(tweet_id="999", full_text=text, author_handle=handle, author_name=name)


def test_parse_reads_possibly_sensitive():
    """推文的 legacy.possibly_sensitive 要冒泡成打码标记"""
    payload = make_payload("hi")
    payload["data"]["tweetResult"]["result"]["legacy"]["possibly_sensitive"] = True
    assert Twitter().parse(payload).is_sensitive is True


def test_parse_without_sensitive_flag_is_false():
    """没有标记的普通推文不应被打码"""
    assert Twitter().parse(make_payload("hi")).is_sensitive is False


def test_parser_forwards_sensitive_flag():
    tweet = TwitterTweet(tweet_id="1", full_text="x", is_sensitive=True)
    assert asyncio.run(TwitterParser.media_parse(tweet)).is_sensitive is True


def test_parse_reads_reply_target_id():
    assert Twitter().parse(make_payload("hi", reply_to_id="999")).reply_to_id == "999"


def test_parse_without_reply_has_empty_id():
    assert Twitter().parse(make_payload("hi")).reply_to_id == ""


def test_parse_reads_author_handle():
    assert Twitter().parse(make_payload("hi", handle="someone")).author_handle == "someone"


def test_quote_renders_markdown_blockquote():
    tweet = TwitterTweet(tweet_id="1", full_text="mine", reply_to=reply_tweet("line1\nline2", handle="other"))
    assert TwitterParser._build_quote(tweet) == (
        '> <i><a href="https://x.com/other">@other</a>：</i>\n> <i>line1</i>\n> <i>line2</i>\n\n'
    )


def test_quote_marks_blank_lines():
    tweet = TwitterTweet(tweet_id="1", reply_to=reply_tweet("a\n\nb"))
    assert TwitterParser._build_quote(tweet) == (
        '> <i><a href="https://x.com/other">@other</a>：</i>\n> <i>a</i>\n>\n> <i>b</i>\n\n'
    )


def test_quote_falls_back_to_author_name():
    tweet = TwitterTweet(tweet_id="1", reply_to=reply_tweet("x", handle="", name="夏吉ゆうこ"))
    assert TwitterParser._build_quote(tweet) == "> <i>夏吉ゆうこ：</i>\n> <i>x</i>\n\n"


def test_quote_without_author():
    tweet = TwitterTweet(tweet_id="1", reply_to=reply_tweet("x", handle="", name=""))
    assert TwitterParser._build_quote(tweet) == "> <i>x</i>\n\n"


def test_quote_skipped_without_reply():
    assert TwitterParser._build_quote(TwitterTweet(tweet_id="1", full_text="mine")) == ""


def test_quote_skipped_when_reply_has_no_text():
    tweet = TwitterTweet(tweet_id="1", full_text="mine", reply_to=reply_tweet(""))
    assert TwitterParser._build_quote(tweet) == ""


def test_quote_skipped_when_reply_text_is_only_media_short_url():
    reply = TwitterTweet(
        tweet_id="999",
        full_text="https://t.co/MediaTail",
        media=[object()],  # 带媒体时 __init__ 会删掉结尾的媒体短链
        author_handle="other",
    )
    assert TwitterParser._build_quote(TwitterTweet(tweet_id="1", reply_to=reply)) == ""


def test_media_parse_prefixes_quote():
    tweet = TwitterTweet(tweet_id="1", full_text="mine", reply_to=reply_tweet("original", handle="other"))
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.content == '> <i><a href="https://x.com/other">@other</a>：</i>\n> <i>original</i>\n\nmine'


def test_media_parse_without_reply_is_unchanged():
    result = asyncio.run(TwitterParser.media_parse(TwitterTweet(tweet_id="1", full_text="mine")))
    assert result.content == "mine"


def test_rich_text_parse_keeps_quote():
    from parsehub.provider_api.twitter import TwitterArticle

    tweet = TwitterTweet(
        tweet_id="1",
        article=TwitterArticle(title="T", content="# Body"),
        reply_to=reply_tweet("original", handle="other"),
    )
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.markdown_content == '> <i><a href="https://x.com/other">@other</a>：</i>\n> <i>original</i>\n\n# Body'


def test_fetch_tweet_attaches_reply_target():
    main = make_payload("mine", reply_to_id="999")
    original = make_payload("original", handle="other")
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, original])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.reply_to_id == "999"
    assert tweet.reply_to is not None
    assert tweet.reply_to.full_text == "original"


def test_fetch_tweet_makes_single_request_when_not_a_reply():
    fetch = AsyncMock(return_value=make_payload("mine"))
    with patch.object(Twitter, "_fetch_result", new=fetch):
        asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert fetch.await_count == 1


def test_fetch_tweet_ignores_reply_fetch_failure():
    main = make_payload("mine", reply_to_id="999")
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, RuntimeError("boom")])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    assert tweet.full_text == "mine"
    assert tweet.reply_to is None


def test_parser_restores_short_url_inside_quote():
    main = make_payload("mine", reply_to_id="999")
    original = {
        "data": {
            "tweetResult": {
                "result": {
                    "rest_id": "999",
                    "legacy": {
                        "full_text": "see https://t.co/abc",
                        "entities": {
                            "urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com/x"}],
                            "media": [],
                        },
                    },
                    "core": {"user_results": {"result": {"legacy": {"name": "Other", "screen_name": "other"}}}},
                }
            }
        }
    }
    with patch.object(Twitter, "_fetch_result", new=AsyncMock(side_effect=[main, original])):
        tweet = asyncio.run(Twitter().fetch_tweet("https://x.com/u/status/1"))
    result = asyncio.run(TwitterParser.media_parse(tweet))
    assert result.content == '> <i><a href="https://x.com/other">@other</a>：</i>\n> <i>see https://example.com/x</i>\n\nmine'
