"""引用块里的**硬换行**：行尾两空格必须留在 ``<i>`` 外面。

markdown 的硬换行是"行尾两个空格 + 换行"。``format_quote_block`` 给整行包 ``<i>…</i>``
做斜体 —— 如果那两空格被包进标签里，行尾字符变成 ``</i>``，服务端不再当硬换行，
**多行会被并成一行**。

真机实测（读回服务端块结构）::

    > <i>行1  </i>      ← 两空格在标签内 → Paragraph[1 行] ❌
    > <i>行2</i>

    > <i>行1</i>        ← 两空格在标签外 → Paragraph[2 行] ✓
    > <i>行2</i>

踩到的场景：bgm 章节页的主楼是官方章节信息（``div.epDesc``：时长/首播 + 简介 + STAFF），
它靠 ``<br/>`` 分行 —— 标记被包进标签里的话整段会被并成一行。
"""

from parsehub.utils.helpers import _quote_line, format_quote_block

# ── 单行 ───────────────────────────────────────────────────────────────

def test_a_plain_line_is_italicised():
    assert _quote_line("正文") == "> <i>正文</i>"


def test_a_hard_break_marker_stays_outside_the_italic_tag():
    """**核心**: 行尾两空格留在 ``</i>`` **之后**"""
    assert _quote_line("行1  ") == "> <i>行1</i>  "


def test_a_blank_line_stays_a_bare_marker():
    """空行仍是光秃秃的 ``>``（引用块靠它分段）"""
    assert _quote_line("") == ">"
    assert _quote_line("   ") == ">"


def test_the_marker_is_kept_verbatim():
    """行尾空白原样保留（markdownify 的 br 给的是两空格，但别假定恰好两格）"""
    assert _quote_line("行  ") == "> <i>行</i>  "
    assert _quote_line("行 ") == "> <i>行</i> "


# ── 整块 ───────────────────────────────────────────────────────────────

def test_the_block_keeps_every_line_separate():
    """整块渲染：每一行的标记都在标签外，行数不丢"""
    block = format_quote_block("行1  \n行2  \n行3")
    lines = block.rstrip("\n").splitlines()
    assert lines == ["> <i>行1</i>  ", "> <i>行2</i>  ", "> <i>行3</i>"], lines


def test_the_author_line_has_no_trailing_marker():
    """署名行不带硬换行标记（它不是正文行）"""
    block = format_quote_block("正文", "作者")
    assert block.splitlines()[0] == "> <i>作者</i>"


def test_a_block_without_markers_is_unchanged():
    """**回归**: 没有硬换行的正文逐字不变"""
    assert format_quote_block("单行正文") == "> <i>单行正文</i>\n\n"
    assert format_quote_block("两段\n\n二") == "> <i>两段</i>\n>\n> <i>二</i>\n\n"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
