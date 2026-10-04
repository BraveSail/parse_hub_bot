"""只解析纯链接消息: 群里随口提到链接不该被解析。

判定在 `url_only_message_urls`: 按空白切分后, 每个片段本身都要是一个完整链接。
"""

from parsehub.utils.helpers import url_only_message_urls

# ── 应当解析 ────────────────────────────────────────────────────────────

def test_single_url():
    assert url_only_message_urls("https://x.com/a/status/123") == ["https://x.com/a/status/123"]


def test_surrounding_whitespace_is_fine():
    assert url_only_message_urls("  https://x.com/a/status/123  ") == ["https://x.com/a/status/123"]
    assert url_only_message_urls("https://x.com/a/status/123\n") == ["https://x.com/a/status/123"]


def test_multiple_urls():
    assert url_only_message_urls("https://x.com/a/status/1 https://b23.tv/abc") == [
        "https://x.com/a/status/1",
        "https://b23.tv/abc",
    ]


def test_multiple_urls_on_separate_lines():
    assert url_only_message_urls("https://x.com/a/status/1\nhttps://b23.tv/abc") == [
        "https://x.com/a/status/1",
        "https://b23.tv/abc",
    ]


def test_trailing_punctuation_is_trimmed():
    """句末加句号很常见, 不该因此漏解析"""
    assert url_only_message_urls("https://x.com/a/status/123。") == ["https://x.com/a/status/123"]
    assert url_only_message_urls("https://x.com/a/status/123,") == ["https://x.com/a/status/123"]


# ── 应当跳过 ────────────────────────────────────────────────────────────

def test_text_before_url_is_rejected():
    assert url_only_message_urls("看看这个 https://x.com/a/status/123") == []


def test_text_after_url_is_rejected():
    assert url_only_message_urls("https://x.com/a/status/123 很好笑") == []


def test_url_between_texts_is_rejected():
    assert url_only_message_urls("https://x.com/a/status/1 中间文字 https://x.com/b/status/2") == []


def test_emoji_prefix_is_rejected():
    assert url_only_message_urls("🤣https://x.com/a/status/123") == []


def test_wrapped_in_brackets_is_rejected():
    assert url_only_message_urls("(https://x.com/a/status/123)") == []


def test_share_blurb_is_rejected():
    """各平台"分享文案 + 链接"的形态不算纯链接消息"""
    assert url_only_message_urls("【分享】https://x.com/a/status/123") == []


def test_plain_text_is_rejected():
    assert url_only_message_urls("今天天气不错") == []


def test_empty_inputs():
    for value in ("", "   ", "\n", None):
        assert url_only_message_urls(value) == []


def test_punctuation_only_token():
    assert url_only_message_urls("。") == []


def test_at_mention_then_url_is_rejected():
    assert url_only_message_urls("@someone https://x.com/a/status/123") == []
