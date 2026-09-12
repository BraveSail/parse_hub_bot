"""推文/帖子正文里的 Markdown 引用块应转成 Telegram 的 blockquote。"""

from plugins.helpers import build_caption_by_str, convert_markdown_quote, format_text


def test_quote_lines_become_blockquote():
    assert convert_markdown_quote("> a\n> b\n\nmine") == "<blockquote>a\nb</blockquote>\n\nmine"


def test_quote_block_without_space_after_marker():
    assert convert_markdown_quote(">a\n\nx") == "<blockquote>a</blockquote>\n\nx"


def test_plain_text_unchanged():
    assert convert_markdown_quote("just text") == "just text"


def test_inline_gt_is_not_converted():
    assert convert_markdown_quote("a > b") == "a > b"


def test_multiple_quote_blocks():
    text = "> one\n\ntext\n\n> two"
    assert convert_markdown_quote(text) == "<blockquote>one</blockquote>\n\ntext\n\n<blockquote>two</blockquote>"


def test_format_text_does_not_nest_blockquote():
    out = format_text("> 回复 @user：\n> " + "x" * 600)
    assert out.count("<blockquote") == 1
    assert "expandable" not in out


def test_format_text_folds_long_plain_text():
    assert format_text("y" * 600) == "<blockquote expandable>" + "y" * 600 + "</blockquote>"


def test_format_text_keeps_short_text_plain():
    assert format_text("short") == "short"


def test_truncation_happens_before_html_conversion():
    out = format_text("> " + "z" * 1500)
    assert out.endswith("......</blockquote>")
    assert out.count("<blockquote") == 1


def test_allow_blockquote_false_keeps_markdown_prefix():
    assert convert_markdown_quote("> a\n\nb", allow_blockquote=False) == "> a\n\nb"


def test_format_text_without_blockquote_does_not_wrap_long_text():
    out = format_text("> " + "x" * 600, allow_blockquote=False)
    assert "<blockquote" not in out
    assert out.startswith("> ")


def test_caption_without_blockquote_keeps_plain_quote_lines():
    caption = build_caption_by_str(
        "",
        "> \u56de\u590d @u\uff1a\n> hi\n\nbody",
        "https://x.com/u/status/1",
        allow_blockquote=False,
    )
    assert "<blockquote" not in caption
    assert "> \u56de\u590d @u\uff1a" in caption
    assert "Source\uff08Twitter\uff09</a>" in caption


def test_caption_uses_blockquote_by_default():
    caption = build_caption_by_str("", "> \u56de\u590d @u\uff1a\n> hi", "https://x.com/u/status/1")
    assert "<blockquote>" in caption


def test_inline_cached_caption_has_no_blockquote():
    from plugins.parse.inline import build_cached_inline_results
    from repo.settings import SettingsConfig
    from services.cache import CacheEntry, CacheParseResult

    entry = CacheEntry(parse_result=CacheParseResult(content="> \u56de\u590d @u\uff1a\n> hi\n\nbody"))
    results = build_cached_inline_results(entry, "https://x.com/u/status/1", "zh-hans", SettingsConfig())

    texts = [
        r.input_message_content.message_text
        for r in results
        if getattr(r, "input_message_content", None) is not None
        and getattr(r.input_message_content, "message_text", None)
    ]
    assert texts
    assert all("<blockquote" not in t for t in texts)
    assert any("> \u56de\u590d @u\uff1a" in t for t in texts)
