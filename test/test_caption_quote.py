"""推文/帖子正文里的 Markdown 引用块应转成 Telegram 的 blockquote。

inline 通道（plugins/parse/inline.py）的真实参数组合是
`build_caption(..., allow_expandable=False)`：
- 保留原生引用块（DM 与 inline 一致，用户确认 inline 能看到引用块）
- 不做 <blockquote expandable> 折叠
caption 里的 URL 和 handle 会先经 neutralize_markdown* 中和，否则 pyrogram 的
Markdown 解析器会把 `__` 当斜体定界符并插 <i>，直接把 <a href> 写坏。
"""

import asyncio

from plugins.helpers import (
    build_caption_by_str,
    convert_markdown_quote,
    format_text,
    neutralize_markdown,
)

QUOTE_SAMPLE = "> \u56de\u590d @u\uff1a\n> hi\n\nbody"
UNDERSCORE_QUOTE = "> \u56de\u590d @__yuuuumr__\uff1a\n> hi\n\nbody"
UNDERSCORE_URL = "https://x.com/__yuuuumr__/status/2095537240726450480"


def _pyrogram_markdown_parse(text: str) -> dict:
    """用 pyrogram 的 Markdown 解析器解析文本。

    client.parse_mode 默认 DEFAULT(markdown+HTML)，与 pyrogram 发送
    InputTextMessageContent / inline 结果时走的路径一致。
    """
    from pyrogram.parser.markdown import Markdown

    return asyncio.run(Markdown(None).parse(text))


def _entity_types(parsed: dict) -> list[str]:
    return [type(e).__name__ for e in (parsed.get("entities") or [])]


def _text_urls(parsed: dict) -> list[str]:
    return [e.url for e in (parsed.get("entities") or []) if type(e).__name__ == "MessageEntityTextUrl"]


def inline_caption(content: str, url: str, **kwargs) -> str:
    """模拟 inline 通道的真实调用参数。"""
    return build_caption_by_str("", content, url, allow_expandable=False, **kwargs)


# ── Markdown 引用 → blockquote ──────────────────────────────────────────────

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


def test_format_text_folds_long_quote_without_nesting():
    """超长引用块自行折叠, 但不外包第二层 blockquote (TG 不支持嵌套)."""
    out = format_text("> \u56de\u590d @user\uff1a\n> " + "x" * 600)
    assert out.count("<blockquote") == 1
    assert "<blockquote expandable>" in out


def test_format_text_keeps_short_quote_unfolded():
    """短引用块保持展开 (与正文同一阈值)."""
    out = format_text("> \u56de\u590d @u\uff1a\n> hi\n\nbody")
    assert out.startswith("<blockquote>")
    assert "expandable" not in out


def test_quote_and_body_fold_independently():
    """引用块与正文各自按同一阈值折叠, 互不外包 (总长控制在截断线内)."""
    out = format_text("> \u56de\u590d @u\uff1a\n> " + "q" * 400 + "\n\n" + "b" * 400)
    assert out.count("<blockquote") == 2
    assert out.count("<blockquote expandable>") == 2
    assert "......" not in out  # 未触发 1000 字符截断


def test_quote_not_folded_when_expandable_disabled():
    out = format_text("> " + "x" * 600, allow_expandable=False)
    assert "<blockquote expandable>" not in out
    assert "<blockquote>" in out


def test_format_text_folds_long_plain_text():
    assert format_text("y" * 600) == "<blockquote expandable>" + "y" * 600 + "</blockquote>"


def test_format_text_keeps_short_text_plain():
    assert format_text("short") == "short"


def test_build_rich_markdown_folds_a_long_body():
    """富文本正文也要折叠 —— 以前只折引用块, 长正文会被撑满整屏且收不起来"""
    import types

    from plugins.helpers import build_rich_markdown

    result = types.SimpleNamespace(
        title="",
        content="很长的正文。" * 60,   # 远超 200 字阈值
        raw_url="https://x.com/a/status/1",
        author_name="",
        author_handle="",
        author_url="",
        published_at=None,
        view_count=None,
        like_count=None,
        tags=None,
        platform=None,
        media=None,
    )
    config = types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)
    md = build_rich_markdown(result, config=config, lang="zh-hans")
    assert "<blockquote expandable>" in md
    assert "很长的正文" in md


def test_build_rich_markdown_keeps_a_short_body_plain():
    import types

    from plugins.helpers import build_rich_markdown

    result = types.SimpleNamespace(
        title="",
        content="短正文",
        raw_url="https://x.com/a/status/1",
        author_name="",
        author_handle="",
        author_url="",
        published_at=None,
        view_count=None,
        like_count=None,
        tags=None,
        platform=None,
        media=None,
    )
    config = types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)
    md = build_rich_markdown(result, config=config, lang="zh-hans")
    assert "expandable" not in md


def test_truncation_happens_before_html_conversion():
    out = format_text("> " + "z" * 1500)
    assert out.endswith("......</blockquote>")
    assert out.count("<blockquote") == 1


def test_format_text_without_expandable_does_not_wrap_long_text():
    out = format_text("x" * 600, allow_expandable=False)
    assert "<blockquote" not in out
    assert out.startswith("x" * 600)


def test_allow_blockquote_false_strips_markdown_prefix():
    assert convert_markdown_quote("> a\n\nb", allow_blockquote=False) == "a\n\nb"


# ── inline 通道: 引用块 + 链接都要在 ────────────────────────────────────────

def test_inline_caption_keeps_blockquote_and_source_link():
    caption = inline_caption(UNDERSCORE_QUOTE, UNDERSCORE_URL)
    parsed = _pyrogram_markdown_parse(caption)

    types = _entity_types(parsed)
    assert "MessageEntityBlockquote" in types          # 引用块在
    assert _text_urls(parsed) == [UNDERSCORE_URL]      # Source 链接完好
    assert "MessageEntityItalic" not in types          # handle 下划线没被当斜体
    assert "@__yuuuumr__" in parsed["message"]


def test_inline_caption_is_not_wrapped_in_expandable():
    caption = inline_caption(UNDERSCORE_QUOTE, UNDERSCORE_URL)
    assert "<blockquote expandable>" not in caption


def test_dm_caption_keeps_blockquote_and_source_link():
    caption = build_caption_by_str("", UNDERSCORE_QUOTE, UNDERSCORE_URL)
    parsed = _pyrogram_markdown_parse(caption)

    types = _entity_types(parsed)
    assert "MessageEntityBlockquote" in types
    assert _text_urls(parsed) == [UNDERSCORE_URL]
    assert "MessageEntityItalic" not in types


def test_inline_cached_rich_result_keeps_blockquote_and_link():
    """缓存路径同样走富文本: 引用块与 Source 链接都不能丢"""
    from plugins.parse.inline import build_cached_rich_result
    from repo.settings import SettingsConfig
    from services.cache import CacheEntry, CacheParseResult

    entry = CacheEntry(parse_result=CacheParseResult(content=UNDERSCORE_QUOTE))
    item = build_cached_rich_result(entry, UNDERSCORE_URL, "zh-hans", SettingsConfig())
    markdown = item.input_message_content.rich_message.markdown

    assert "> " in markdown  # 引用块语法
    assert UNDERSCORE_URL in markdown
    assert "Source" in markdown


# ── markdown 定界符中和 ─────────────────────────────────────────────────────

def test_neutralize_markdown_keeps_text_readable():
    assert neutralize_markdown("@__user__ **bold** ~~x~~") != "@__user__ **bold** ~~x~~"
    parsed = _pyrogram_markdown_parse(neutralize_markdown("@__user__"))
    assert parsed["message"] == "@__user__"


def test_quote_prefix_would_otherwise_become_blockquote_entity():
    """反证: pyrogram 确实会把行首 '>' 解析成 blockquote 实体。"""
    parsed = _pyrogram_markdown_parse("> \u56de\u590d @u\uff1a\n> hi\n\nbody")
    assert "MessageEntityBlockquote" in _entity_types(parsed)


def test_author_name_underscores_survive_markdown_parse():
    caption = build_caption_by_str("", "body", "https://x.com/u/status/1", author_name="foo__bar")
    parsed = _pyrogram_markdown_parse(caption)
    assert "MessageEntityItalic" not in _entity_types(parsed)
    assert "foo__bar" in parsed["message"]


def test_body_url_with_underscores_survives():
    body = "see https://example.com/a__b__c/page for details"
    caption = inline_caption(body, "https://x.com/u/status/1")
    parsed = _pyrogram_markdown_parse(caption)
    assert "https://example.com/a__b__c/page" in parsed["message"]
    assert "MessageEntityItalic" not in _entity_types(parsed)
