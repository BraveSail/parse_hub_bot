"""推文/帖子正文里的 Markdown 引用块应转成 Telegram 的 blockquote。"""

from plugins.helpers import convert_markdown_quote, format_text


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
