"""推文/帖子正文里的 Markdown 引用块应转成 Telegram 的 blockquote。

注意: pyrogram 的 Markdown 解析器会把行首 '>' 重新解析成 blockquote 实体,
而 Telegram 内联消息遇到 blockquote 实体会丢掉整批格式, 所以内联通道
(allow_blockquote=False) 必须把 '>' 前缀整个去掉 —— 见
test_inline_caption_parses_without_blockquote_entity。
"""

import asyncio

from plugins.helpers import build_caption_by_str, convert_markdown_quote, format_text

QUOTE_SAMPLE = "> \u56de\u590d @u\uff1a\n> hi\n\nbody"


def _pyrogram_markdown_parse(text: str) -> dict:
    """用 pyrogram 的 Markdown 解析器解析文本, 返回 {message, entities}。

    pyrogram 的 client.parse_mode 默认是 DEFAULT, 与内联消息里
    InputTextMessageContent 未显式指定 parse_mode 时走的路径一致。
    """
    from pyrogram.parser.markdown import Markdown

    return asyncio.run(Markdown(None).parse(text))


def _entity_types(parsed: dict) -> list[str]:
    return [type(e).__name__ for e in (parsed.get("entities") or [])]


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
    out = format_text("> \u56de\u590d @user\uff1a\n> " + "x" * 600)
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


def test_allow_blockquote_false_strips_markdown_prefix():
    assert convert_markdown_quote("> a\n\nb", allow_blockquote=False) == "a\n\nb"


def test_format_text_without_blockquote_does_not_wrap_long_text():
    out = format_text("> " + "x" * 600, allow_blockquote=False)
    assert "<blockquote" not in out
    assert not out.startswith(">")
    assert out.startswith("x" * 600)


def test_caption_without_blockquote_keeps_quote_content():
    caption = build_caption_by_str(
        "",
        QUOTE_SAMPLE,
        "https://x.com/u/status/1",
        allow_blockquote=False,
    )
    assert "<blockquote" not in caption
    assert "\u56de\u590d @u\uff1a" in caption
    assert "hi" in caption
    assert "Source\uff08Twitter\uff09</a>" in caption


def test_caption_uses_blockquote_by_default():
    caption = build_caption_by_str("", "> \u56de\u590d @u\uff1a\n> hi", "https://x.com/u/status/1")
    assert "<blockquote>" in caption


def test_inline_caption_parses_without_blockquote_entity():
    """回归: 内联 caption 经 pyrogram 的 Markdown 解析后不能出现 blockquote 实体。"""
    caption = build_caption_by_str("", QUOTE_SAMPLE, "https://x.com/u/status/1", allow_blockquote=False)
    parsed = _pyrogram_markdown_parse(caption)

    types = _entity_types(parsed)
    assert "MessageEntityBlockquote" not in types
    # 来源链接必须还在
    assert "MessageEntityTextUrl" in types
    assert "MessageEntityBold" in types
    # 引用内容以纯文本保留
    assert "\u56de\u590d @u\uff1a" in parsed["message"]
    assert not parsed["message"].startswith(">")


def test_quote_prefix_would_otherwise_become_blockquote_entity():
    """反证: 保留 '>' 前缀时 pyrogram 确实会造出 blockquote 实体。"""
    parsed = _pyrogram_markdown_parse("> \u56de\u590d @u\uff1a\n> hi\n\nbody")
    assert "MessageEntityBlockquote" in _entity_types(parsed)


def test_inline_cached_caption_has_no_blockquote():
    from plugins.parse.inline import build_cached_inline_results
    from repo.settings import SettingsConfig
    from services.cache import CacheEntry, CacheParseResult

    entry = CacheEntry(parse_result=CacheParseResult(content=QUOTE_SAMPLE))
    results = build_cached_inline_results(entry, "https://x.com/u/status/1", "zh-hans", SettingsConfig())

    texts = [
        r.input_message_content.message_text
        for r in results
        if getattr(r, "input_message_content", None) is not None
        and getattr(r.input_message_content, "message_text", None)
    ]
    assert texts
    assert all("<blockquote" not in t for t in texts)
    assert any("\u56de\u590d @u\uff1a" in t for t in texts)
    assert all("MessageEntityBlockquote" not in _entity_types(_pyrogram_markdown_parse(t)) for t in texts)


UNDERSCORE_SAMPLE = "> \u56de\u590d @__yuuuumr__\uff1a\n> hi\n\nbody"


def test_quote_handle_underscores_survive_markdown_parse():
    """回归: @__user__ 这类 handle 不能被 markdown 当斜体、把下划线吃掉。"""
    inline = build_caption_by_str("", UNDERSCORE_SAMPLE, "https://x.com/u/status/1", allow_blockquote=False)
    parsed_inline = _pyrogram_markdown_parse(inline)
    assert "MessageEntityItalic" not in _entity_types(parsed_inline)
    assert "@__yuuuumr__" in parsed_inline["message"]

    dm = build_caption_by_str("", UNDERSCORE_SAMPLE, "https://x.com/u/status/1")
    parsed_dm = _pyrogram_markdown_parse(dm)
    assert "MessageEntityItalic" not in _entity_types(parsed_dm)
    assert "@__yuuuumr__" in parsed_dm["message"]
    assert "MessageEntityBlockquote" in _entity_types(parsed_dm)


def test_author_name_underscores_survive_markdown_parse():
    caption = build_caption_by_str("", "body", "https://x.com/u/status/1", author_name="foo__bar")
    parsed = _pyrogram_markdown_parse(caption)
    assert "MessageEntityItalic" not in _entity_types(parsed)
    assert "foo__bar" in parsed["message"]


def test_neutralize_markdown_keeps_text_readable():
    from plugins.helpers import neutralize_markdown

    assert neutralize_markdown("@__user__ **bold** ~~x~~") != "@__user__ **bold** ~~x~~"
    caption = neutralize_markdown("@__user__")
    parsed = _pyrogram_markdown_parse(caption)
    assert parsed["message"] == "@__user__"


UNDERSCORE_URL = "https://x.com/__yuuuumr__/status/2095537240726450480"


def _text_urls(parsed: dict) -> list[str]:
    return [e.url for e in (parsed.get("entities") or []) if type(e).__name__ == "MessageEntityTextUrl"]


def test_source_link_survives_underscored_url_inline():
    """回归: handle 带 __ 的 URL 不能被 markdown 解析插进 <i> 把 href 写坏。"""
    caption = build_caption_by_str("", QUOTE_SAMPLE, UNDERSCORE_URL, allow_blockquote=False)
    parsed = _pyrogram_markdown_parse(caption)
    assert _text_urls(parsed) == [UNDERSCORE_URL]
    assert "MessageEntityItalic" not in _entity_types(parsed)


def test_source_link_survives_underscored_url_dm():
    caption = build_caption_by_str("", QUOTE_SAMPLE, UNDERSCORE_URL)
    parsed = _pyrogram_markdown_parse(caption)
    assert _text_urls(parsed) == [UNDERSCORE_URL]
    assert "MessageEntityItalic" not in _entity_types(parsed)


def test_body_url_with_underscores_survives():
    body = "see https://example.com/a__b__c/page for details"
    caption = build_caption_by_str("", body, "https://x.com/u/status/1", allow_blockquote=False)
    parsed = _pyrogram_markdown_parse(caption)
    assert "https://example.com/a__b__c/page" in parsed["message"]
    assert "MessageEntityItalic" not in _entity_types(parsed)
