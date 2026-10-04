"""引用/回复块的媒体归位。

被回复的卡片在正文前、被引用的卡片在正文后, 各自的媒体必须留在自己的块里
(占位符不带 `> ` 前缀就会掉到块外面, 变成独立的图片块)。
"""

import types

from plugins.helpers import attach_quote_media, build_rich_markdown, split_quote_blocks


class _Cfg(types.SimpleNamespace):
    hide_title = False
    hide_desc = False
    hide_source = True


def _result(**kw):
    result = types.SimpleNamespace(
        title="",
        content="",
        raw_url="https://x.com/a/status/1",
        author_name="作者",
        author_handle="",
        author_url="",
        published_at=None,
        view_count=None,
        like_count=None,
        tags=None,
        platform=None,
        media=None,
    )
    for k, v in kw.items():
        setattr(result, k, v)
    return result


# ── split_quote_blocks ──────────────────────────────────────────────────

def test_split_takes_both_ends():
    head, middle, tail = split_quote_blocks(
        "> <i>回复的作者：</i>\n> <i>回复内容</i>\n\n正文\n\n> <i>引用的作者：</i>\n> <i>引用内容</i>"
    )
    assert head == "> <i>回复的作者：</i>\n> <i>回复内容</i>"
    assert middle == "正文"
    assert tail == "> <i>引用的作者：</i>\n> <i>引用内容</i>"


def test_split_without_quotes():
    head, middle, tail = split_quote_blocks("只有正文")
    assert (head, middle, tail) == ("", "只有正文", "")


def test_split_only_trailing():
    head, middle, tail = split_quote_blocks("正文\n\n> <i>引用内容</i>")
    assert head == ""
    assert middle == "正文"
    assert tail == "> <i>引用内容</i>"


def test_split_single_quote_block_is_treated_as_the_trailing_one():
    """只有一块引用时归为末尾块 (它本来就在最后), 不能判成"开头块 + 空正文"而丢内容"""
    head, middle, tail = split_quote_blocks("> <i>只有引用</i>")
    assert head == ""
    assert middle == ""
    assert tail == "> <i>只有引用</i>"


def test_reply_media_never_dropped_when_the_block_is_ambiguous():
    """只有一块引用时, 回复媒体要落到那块里 —— 绝不能凭空消失"""
    md = build_rich_markdown(
        _result(content="> <i>只有引用</i>"),
        config=_Cfg(),
        lang="zh-hans",
        reply_media_placeholders=["![](tg://photo?id=reply)"],
    )
    assert "> ![](tg://photo?id=reply)" in md


def test_body_media_never_dropped_without_any_quote_block():
    """没有引用块时正文媒体照常出现"""
    md = build_rich_markdown(
        _result(content="正文"),
        config=_Cfg(),
        lang="zh-hans",
        media_placeholders=["![](tg://photo?id=body)"],
    )
    assert "![](tg://photo?id=body)" in md


# ── attach_quote_media ──────────────────────────────────────────────────

def test_attach_adds_the_quote_prefix():
    out = attach_quote_media("> <i>引用文字</i>", ["![](tg://photo?id=m0)"])
    assert out == "> <i>引用文字</i>\n> ![](tg://photo?id=m0)"


def test_attach_wraps_multiple_in_a_collage():
    out = attach_quote_media("> <i>引用文字</i>", ["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"])
    assert "> <tg-collage>" in out
    assert "> ![](tg://photo?id=m0)" in out
    assert "> ![](tg://photo?id=m1)" in out


def test_attach_without_media_is_identity():
    assert attach_quote_media("> <i>引用文字</i>", []) == "> <i>引用文字</i>"


def test_attach_keeps_every_line_inside_the_block():
    """每行都要带 > , 否则会被踢出引用块"""
    out = attach_quote_media("> <i>引用文字</i>", ["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"])
    assert all(line.startswith(">") for line in out.split("\n") if line.strip())


# ── build_rich_markdown 端到端 ──────────────────────────────────────────

def test_reply_and_quoted_media_land_in_their_blocks():
    content = "> <i>回复者 @a：</i>\n> <i>被回复的话</i>\n\n正文内容\n\n> <i>引用者 @b：</i>\n> <i>被引用的话</i>"
    md = build_rich_markdown(
        _result(content=content),
        config=_Cfg(),
        lang="zh-hans",
        media_placeholders=["![](tg://photo?id=body)"],
        quote_media_placeholders=["![](tg://photo?id=quoted)"],
        reply_media_placeholders=["![](tg://photo?id=reply)"],
    )
    lines = md.split("\n")
    i_reply_media = lines.index("> ![](tg://photo?id=reply)")
    i_body_media = lines.index("![](tg://photo?id=body)")
    i_quoted_media = lines.index("> ![](tg://photo?id=quoted)")
    # 回复块(含其媒体) → 正文媒体 → 引用块(含其媒体)
    assert i_reply_media < i_body_media < i_quoted_media
    # 三个块各自的位置也要对
    assert lines.index("> <i>被回复的话</i>") < i_reply_media
    assert lines.index("正文内容") < i_body_media
    assert lines.index("> <i>被引用的话</i>") < i_quoted_media


def test_no_reply_media_keeps_the_body_where_it_was():
    """没有引用媒体时排版不该变 (回归)"""
    md = build_rich_markdown(
        _result(content="正文\n\n> <i>引用</i>"),
        config=_Cfg(),
        lang="zh-hans",
        media_placeholders=["![](tg://photo?id=body)"],
    )
    lines = [line for line in md.split("\n") if line.strip()]
    assert lines.index("正文") < lines.index("![](tg://photo?id=body)") < lines.index("> <i>引用</i>")
