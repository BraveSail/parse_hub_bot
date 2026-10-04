"""富文本 blocks 转换器的离线用例 (纯函数, 不联网/不发送)。"""

from pyrogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockParagraph,
    InputRichBlockPreformatted,
    InputRichBlockSectionHeading,
)
from pyrogram.types.messages_and_media.rich_text import (
    RichTextBold,
    RichTextCode,
    RichTextStrikethrough,
    RichTextUrl,
)

from plugins.parse.rich_blocks import markdown_to_blocks, parse_inline


def test_parse_inline_plain_text():
    assert parse_inline("普通文字") == "普通文字"


def test_parse_inline_bold():
    parts = parse_inline("前**粗**后")
    assert isinstance(parts[1], RichTextBold)


def test_parse_inline_markdown_link():
    """[文字](url)"""
    parts = parse_inline("看 [文字](https://a.com)")
    link = next(p for p in parts if isinstance(p, RichTextUrl))
    assert link.text == "文字"
    assert link.url == "https://a.com"


def test_parse_inline_html_link():
    """footer 里的来源链接是 HTML: markdown 链接语法在 footer 块里不解析"""
    parts = parse_inline('14:37 · 32,846 查看 · <a href="https://x.com/u/status/1">来源（Twitter）</a>')
    link = next(p for p in parts if isinstance(p, RichTextUrl))
    assert link.text == "来源（Twitter）"
    assert link.url == "https://x.com/u/status/1"


def test_parse_inline_html_link_unescapes_entities():
    parts = parse_inline('<a href="https://c.com?a=1&amp;b=2">x &amp; y</a>')
    link = next(p for p in parts if isinstance(p, RichTextUrl))
    assert link.url == "https://c.com?a=1&b=2"
    assert link.text == "x & y"


def test_parse_inline_code_and_strikethrough():
    assert isinstance(parse_inline("`码`")[0], RichTextCode)
    assert isinstance(parse_inline("~~删~~")[0], RichTextStrikethrough)


def test_blocks_heading_and_divider_and_footer():
    blocks = markdown_to_blocks("### 标题\n\n---\n\n<footer>页尾</footer>")
    assert isinstance(blocks[0], InputRichBlockSectionHeading)
    assert blocks[0].size == 3
    assert isinstance(blocks[1], InputRichBlockDivider)
    assert isinstance(blocks[2], InputRichBlockFooter)


def test_blocks_consecutive_lines_stay_one_paragraph():
    """连续非空行必须合并成一个段落, 不能一行一个块"""
    blocks = markdown_to_blocks("第一行\n第二行\n第三行")
    assert len(blocks) == 1
    assert isinstance(blocks[0], InputRichBlockParagraph)


def test_blocks_quote_and_list():
    blocks = markdown_to_blocks("> 引用一\n> 引用二\n\n- 项一\n- 项二")
    assert isinstance(blocks[0], InputRichBlockBlockQuotation)
    assert isinstance(blocks[1], InputRichBlockList)
    assert len(blocks[1].items) == 2


def test_blocks_checkbox_list():
    blocks = markdown_to_blocks("- [x] 已完成\n- [ ] 未完成")
    items = blocks[0].items
    assert items[0].has_checkbox is True and items[0].is_checked is True
    assert items[1].is_checked is False


def test_blocks_preformatted_keeps_code():
    blocks = markdown_to_blocks("```python\nprint(1)\n```")
    assert isinstance(blocks[0], InputRichBlockPreformatted)
    assert "print(1)" in blocks[0].text
    assert blocks[0].language == "python"


def test_blocks_media_placeholder_uses_given_block():
    blocks = markdown_to_blocks("正文\n\n![](tg://photo?id=m0)\n\n后文", media_blocks={"m0": "MEDIA"})
    assert blocks[1] == "MEDIA"


def test_blocks_collage_groups_multiple_media():
    """<tg-collage> 包住的多个媒体要变成一个 Collage 块"""
    from pyrogram.types import InputRichBlockCollage

    md = "正文\n\n<tg-collage>\n\n![](tg://photo?id=m0)\n![](tg://photo?id=m1)\n\n</tg-collage>"
    blocks = markdown_to_blocks(md, media_blocks={"m0": "M0", "m1": "M1"})
    assert isinstance(blocks[1], InputRichBlockCollage)
    assert blocks[1].blocks == ["M0", "M1"]


def test_blocks_collage_with_single_media_flattens():
    """只有一张时不套 Collage (没必要)"""
    md = "<tg-collage>\n\n![](tg://photo?id=m0)\n\n</tg-collage>"
    blocks = markdown_to_blocks(md, media_blocks={"m0": "M0"})
    assert blocks == ["M0"]


def test_blocks_unknown_media_placeholder_is_skipped():
    """占位没有对应媒体块时跳过, 不能把 tg:// 链接漏给用户看"""
    blocks = markdown_to_blocks("正文\n\n![](tg://photo?id=nope)")
    assert len(blocks) == 1
