"""blocks 路径要认得 markdown 表格（敏感内容/引用卡片走它时不能把表格丢成裸文字）。

服务端直接认 markdown 表格语法（已实测 → ``RichBlockTable``），但 **blocks 路径是自己
转的** —— 不认的话表格会变成一串带竖线的普通段落文字。
"""

from plugins.parse.rich_blocks import markdown_to_blocks

TABLE = """| 选项 | 票数 | 占比 |
| --- | --- | --- |
| 甲 | 71 | 32% |
| 乙 | 31 | 14% |
"""


def test_an_inline_spoiler_becomes_a_spoiler_span():
    """行内 `||文字||` → RichTextSpoiler（敏感帖/引用卡片走 blocks 时不能丢遮罩）"""
    from plugins.parse.rich_blocks import markdown_to_blocks

    blocks = markdown_to_blocks("前 ||被遮住的字|| 后")
    kinds = []

    def walk(node, depth=0):  # noqa: ANN001
        if node is None or isinstance(node, (str, int)) or depth > 6:
            return
        kinds.append(type(node).__name__)
        inner = getattr(node, "text", None)
        if inner is not None and not isinstance(inner, str):
            for one in inner if isinstance(inner, list) else [inner]:
                walk(one, depth + 1)
        if isinstance(node, list):
            for one in node:
                walk(one, depth + 1)

    walk(blocks)
    assert "RichTextSpoiler" in kinds, kinds


def test_a_markdown_table_becomes_a_table_block():
    blocks = markdown_to_blocks(TABLE)
    assert len(blocks) == 1, [type(b).__name__ for b in blocks]
    table = blocks[0]
    assert type(table).__name__ == "InputRichBlockTable"
    assert len(table.cells) == 3, "表头 + 两行数据"
    assert [c.text for c in table.cells[0]] == ["选项", "票数", "占比"]
    assert all(c.is_header for c in table.cells[0]), "首行是表头"
    assert [c.text for c in table.cells[1]] == ["甲", "71", "32%"]
    assert not any(c.is_header for c in table.cells[1])


def test_the_separator_row_is_dropped():
    blocks = markdown_to_blocks(TABLE)
    flat = [c.text for row in blocks[0].cells for c in row]
    assert "---" not in flat, flat


def test_a_table_among_other_blocks_keeps_its_position():
    md = "上文\n\n" + TABLE + "\n下文"
    kinds = [type(b).__name__ for b in markdown_to_blocks(md)]
    assert kinds == ["InputRichBlockParagraph", "InputRichBlockTable", "InputRichBlockParagraph"], kinds


def test_a_row_of_pipes_without_a_separator_is_not_a_table():
    """只有一行 `|` 不是表格（避免把正文里的竖线误判）"""
    blocks = markdown_to_blocks("| 这不是表格 |")
    assert all(type(b).__name__ != "InputRichBlockTable" for b in blocks)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
