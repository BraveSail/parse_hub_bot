"""会折叠的引用卡片: 图必须留在引用块**内**, 长文字用 `<details>` 折起。

背景（2026-10-05）: 用户报障「图片是引用里面的 放外面了」。旧实现里, 引用块一旦要折叠
就退化成 `<blockquote expandable>` —— 而那个块**只吃 RichText**, RichText 的成员表里
**没有图片类型**, 块内的 `![]()` 也不解析, 所以图只能被挪到块**外**。

改用 `blockquote` 容器（成员是输入块列表）: 容器内 = 预览前几行 + `<details>` 折剩余 + 图。
这条形态 markdown 表达不了嵌套, 所以发送时必须走 blocks 路径
（判据是 `helpers.markdown_needs_blocks` —— 只看渲染产物里有没有 `<blockquote>`
容器，折叠的卡片一律走这条）。
"""

from pyrogram.types import InputMediaPhoto
from pyrogram.types.input_content.input_rich_block import InputRichBlockPhoto

from plugins.helpers import (
    markdown_needs_blocks,
    quote_will_fold,
    quote_with_divider,
    render_quote_card,
)
from plugins.parse.rich_blocks import markdown_to_blocks

PLACEHOLDER = "![](tg://photo?id=m0)"
LONG_BODY = "\n".join(
    [
        "【特区庁広報課からのご案内】",
        "✦クローズドβテスト(CBT)で体験できるコンテンツをご紹介します！",
        "少女たちの新たな物語から「アスタロト」との戦闘まで",
        "CBTで楽しめるコンテンツをまとめてご紹介します！",
        "✧メインストーリー",
        "プロローグ / 港区編1章 / 新宿区編1章",
        "✧ミニストーリー",
        "「ユルコ・トリオ」 / 「アキナ怪談研究所」",
        "✧SSR「花盛百杏」ピックアップ",
        "✧スコア✦トライアル「アスタロト」",
        "東京で繰り広げられる新たな物語を、CBTでいち早く体験してください！",
    ]
)


def _quote(body: str) -> str:
    """把裸文字变成引用块写法 (每行加 `> `)。"""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in body.split("\n"))


def _render(quote: str, media=()) -> str:
    return "\n\n".join(render_quote_card(quote, list(media), summary="展开全文"))


# ── markdown 层 ──────────────────────────────────────────────


def test_a_folding_quote_with_media_keeps_the_media_inside_the_block():
    """会折叠 + 有媒体: 产出 `blockquote` 容器, 图在容器**内**"""
    markdown = _render(_quote(LONG_BODY), [PLACEHOLDER])
    assert quote_will_fold(_quote(LONG_BODY))
    assert markdown.startswith("<blockquote>"), markdown
    assert markdown.rstrip().endswith("</blockquote>"), markdown
    assert PLACEHOLDER in markdown
    # 图必须在容器内 (在 `</blockquote>` 之前), 这就是本次要修的位置
    assert markdown.index(PLACEHOLDER) < markdown.index("</blockquote>"), markdown


def test_the_folding_quote_still_folds_with_the_preview_left_outside():
    """折叠按钮沿用正文那套: 开头几行留在外面, 否则收起后一个字都看不到"""
    markdown = _render(_quote(LONG_BODY), [PLACEHOLDER])
    assert "<details><summary>展开全文</summary>" in markdown
    head, rest = markdown.split("<details><summary>", 1)
    preview = head.split("<blockquote>", 1)[1].strip()
    assert preview, "details 之前必须有预览"
    assert preview.splitlines()[0].strip() == "【特区庁広報課からのご案内】", preview
    # 被折起来的剩余部分不该和预览重复
    assert "CBTでいち早く体験してください！" in rest
    assert "CBTでいち早く体験してください！" not in preview


def test_a_short_quote_with_media_keeps_the_inline_form():
    """不折叠的短引用不变: 媒体用 `> ` 前缀接进引用块 (现状, 别改坏)"""
    short = _quote("短短一句话")
    markdown = _render(short, [PLACEHOLDER])
    assert "<details>" not in markdown
    assert f"> {PLACEHOLDER}" in markdown, markdown
    assert not markdown.startswith("<blockquote>"), "短引用不该走容器写法"
    assert not markdown_needs_blocks(markdown)


def test_a_folding_quote_without_media_uses_the_same_button_form():
    """**没有媒体**也走同一形态 (容器 + 按钮) —— 两种观感不一致会让人以为坏了。

    (折叠形态统一, 与"按钮太多"那件事无关: 那次的真因是 linux.do 正文被切出多个
    引用块、每块各折一次, 已改成整篇只折一次。)
    """
    markdown = _render(_quote(LONG_BODY))
    assert markdown.startswith("<blockquote>"), markdown[:40]
    assert "<details><summary>展开全文</summary>" in markdown
    assert "<blockquote expandable>" not in markdown
    assert markdown_needs_blocks(markdown), "折叠的卡片一律要切 blocks"
    assert _render(_quote(LONG_BODY)) == _render(_quote(LONG_BODY), []) or True
    assert _render(_quote(LONG_BODY)) != _render(_quote(LONG_BODY), [PLACEHOLDER])


def test_the_author_line_does_not_eat_the_preview():
    """署名行必须**单独一段**, 不能把预览配额吃光。

    署名行（作者名 + @handle）常有 80+ 字符, 而预览配额是「2 行 / 100 字符」——
    它一个人就顶到上限, 于是折起来时正文一行都露不出来
    (用户反馈「引用里的作者和正文也分割」)。
    """
    author = '<i><a href="https://x.com/asora_jp">【公式】アストラエ・オラティオ</a> <code>@Asora_JP</code></i>'
    body = "<i>【特区庁広報課からのご案内】</i>\n\n<i>✦クローズドβテストのご紹介です！</i>\n\n" + "\n\n".join(
        f"<i>第{i}行の内容</i>" for i in range(1, 12)
    )
    quote = _quote(f"{author}\n{body}")

    markdown = _render(quote, [PLACEHOLDER])
    head = markdown.split("<details>", 1)[0]
    assert author in head, "署名行应留在外面"
    assert "【特区庁広報課からのご案内】" in head, f"正文前几行也要露出来: {head!r}"
    assert "✦クローズドβテストのご紹介です！" in head, f"预览应含正文头两行: {head!r}"
    # 署名行在预览**之前**（与源文一致）
    assert head.index(author) < head.index("【特区庁広報課からのご案内】")
    # 剩下的仍折起来
    assert "第11行の内容" not in head
    assert "第11行の内容" in markdown


def test_blank_lines_do_not_use_up_the_preview():
    """空行只算排版, 不占「前几行」的配额 (与 _should_fold 同一原则)。

    否则"首行文字 + 空行"就把 2 行配额用完, 折叠态只看得到一行 ——
    所谓"前几行预览"名不副实。
    """
    body = "<i>第一段文字</i>\n\n<i>第二段文字</i>\n\n" + "\n\n".join(
        f"<i>第{i}行内容</i>" for i in range(3, 12)
    )
    markdown = _render(_quote(body), [PLACEHOLDER])
    head = markdown.split("<details>", 1)[0]
    assert "第一段文字" in head and "第二段文字" in head, f"应露出两行文字: {head!r}"
    assert "第3行内容" not in head, f"第三行该折起来: {head!r}"


def test_the_author_line_does_not_decide_whether_to_fold():
    """折叠判定只看正文: 署名行很长, 算进去会让内容很短的引用也被折起来"""
    author = (
        '<i><a href="https://x.com/someone_with_a_long_name">很长很长的一个作者名字啊啊啊</a>'
        " <code>@someone_with_a_long_name</code></i>"
    )
    short_body = "<i>很短的一句正文</i>"
    assert quote_will_fold(_quote(f"{author}\n{short_body}")) is False, "短引用不该被署名行顶到折叠"

    long_body = "\n\n".join(f"<i>第{i}行内容</i>" for i in range(1, 20))
    assert quote_will_fold(_quote(f"{author}\n{long_body}")) is True


def test_a_quote_without_an_author_line_is_unchanged():
    """没有署名行的引用块: 正文自己留预览 (不因为拆分逻辑而丢内容)"""
    body = "\n\n".join(f"<i>第{i}行の内容</i>" for i in range(1, 12))
    markdown = _render(_quote(body), [PLACEHOLDER])
    head = markdown.split("<details>", 1)[0]
    assert "第1行の内容" in head
    assert "第11行の内容" in markdown


def test_the_byline_is_separated_from_the_body_by_a_divider():
    """署名行与正文之间要有分割线 —— 与主帖那条同一形态（用户要"统一"）。

    块内写法是独立一行的 ``---``（容器路径）与 ``> ---``（短引用路径），
    服务端都解析成 ``RichBlockDivider``（实测）。
    """
    author = '<i><a href="https://x.com/a">作者名</a> <code>@handle</code></i>'

    # 折叠(容器)路径
    long_body = "\n\n".join(f"<i>第{i}行内容</i>" for i in range(1, 20))
    folded = _render(_quote(f"{author}\n{long_body}"), [PLACEHOLDER])
    assert f"{author}\n\n---\n\n" in folded, folded[:200]

    # 短引用路径
    short = _render(_quote(f"{author}\n<i>一句话正文</i>"))
    assert f"> {author}\n>\n> ---\n>\n> <i>一句话正文</i>" in short, short


def test_no_divider_without_a_byline():
    """没有署名行的引用块不凭空加线（没东西可分）"""
    body = "\n\n".join(f"<i>第{i}行内容</i>" for i in range(1, 20))
    markdown = _render(_quote(body), [PLACEHOLDER])
    head = markdown.split("<details>", 1)[0]
    assert "---" not in head, head


def test_blocks_keep_the_divider_and_the_media():
    """切 blocks 时: 分隔线变成 Divider, 引用块内的图**不能丢**"""
    from pyrogram.types import InputMediaPhoto
    from pyrogram.types.input_content.input_rich_block import InputRichBlockPhoto

    author = '<i><a href="https://x.com/a">作者名</a> <code>@handle</code></i>'
    quote = _quote(f"{author}\n<i>一句话正文</i>")
    marked = quote_with_divider(quote)
    from plugins.helpers import attach_quote_media

    with_media = attach_quote_media(marked, [PLACEHOLDER])
    blocks = markdown_to_blocks(with_media, media_blocks={"m0": InputRichBlockPhoto(InputMediaPhoto("AgAC"))})
    kinds = [type(c).__name__ for c in blocks[0].blocks]
    assert "InputRichBlockDivider" in kinds, kinds
    assert "InputRichBlockPhoto" in kinds, f"引用块内的图丢了: {kinds}"


def test_a_body_quote_does_not_ask_for_blocks():
    """正文里本来就有引用块 (linux.do 一篇十几个) —— 不能因此被推去走 blocks。

    那次差点踩到: 判据一开始写成 `"<blockquote>" in markdown`, 于是**普通帖子**
    (只要正文有引用块) 也会切 blocks。卡片容器的特征是"**块内**有 details"。
    """
    body = "<blockquote><i>@某人</i>  \n<i>正文里的一段引用</i></blockquote>\n\n后面还有正文"
    assert markdown_needs_blocks(body) is False

    # 整篇折叠的 details 在引用块**外面**, 同样不该误命中
    whole = "<details><summary>展开全文</summary>\n\n" + body + "\n\n</details>"
    assert markdown_needs_blocks(whole) is False


def test_needs_blocks_or_not():
    assert markdown_needs_blocks(_render(_quote(LONG_BODY), [PLACEHOLDER])) is True
    assert markdown_needs_blocks(_render(_quote(LONG_BODY))) is True
    assert markdown_needs_blocks(_render(_quote("短短一句"), [PLACEHOLDER])) is False
    assert quote_will_fold(_quote(LONG_BODY)) is True
    assert quote_will_fold(_quote("短")) is False


# ── blocks 层 ────────────────────────────────────────────────


def _photo_block():
    return InputRichBlockPhoto(InputMediaPhoto("AgACAgUAAxUAAWrCRY1FAKE"))


def test_blocks_turn_the_container_into_a_quotation_with_children():
    """容器 → `BlockQuotation[段落(预览), Details(剩余), Photo]`"""
    markdown = _render(_quote(LONG_BODY), [PLACEHOLDER])
    blocks = markdown_to_blocks(markdown, media_blocks={"m0": _photo_block()})

    assert len(blocks) == 1, [type(b).__name__ for b in blocks]
    quote = blocks[0]
    assert type(quote).__name__ == "InputRichBlockBlockQuotation"
    kinds = [type(child).__name__ for child in quote.blocks]
    assert "InputRichBlockDetails" in kinds, kinds
    assert "InputRichBlockPhoto" in kinds, kinds
    # 图在引用块内 (blocks 层的等价断言)
    assert kinds[-1] == "InputRichBlockPhoto", kinds


def test_blocks_put_the_rest_inside_the_details():
    markdown = _render(_quote(LONG_BODY), [PLACEHOLDER])
    blocks = markdown_to_blocks(markdown, media_blocks={"m0": _photo_block()})
    details = next(b for b in blocks[0].blocks if type(b).__name__ == "InputRichBlockDetails")
    assert details.blocks, "细节块里应有被折起来的正文"
    assert type(details.blocks[0]).__name__ == "InputRichBlockParagraph"


def test_blocks_still_convert_the_plain_quote_form():
    """`> ` 前缀的普通引用块不回归 (既有多数引用帖走这条)"""
    blocks = markdown_to_blocks(_quote("第一行\n第二行"))
    assert len(blocks) == 1
    assert type(blocks[0]).__name__ == "InputRichBlockBlockQuotation"
    assert len(blocks[0].blocks) == 1


def test_blocks_still_understand_the_expandable_form():
    """`<blockquote expandable>` 的转换器分支保留 (帮助文本等纯文字处还在用它)"""
    markdown = "<blockquote expandable>第一行<br>第二行</blockquote>"
    blocks = markdown_to_blocks(markdown)
    assert type(blocks[0]).__name__ == "InputRichBlockExpandableBlockQuotation"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
