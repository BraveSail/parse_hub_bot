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
    assert out.count("<blockquote expandable>") == 1   # 整块一起折叠, 不外包第二层
    assert out.count("<blockquote") == 1
    assert "<details>" not in out


def test_format_text_keeps_short_quote_unfolded():
    """短引用块保持展开 (与正文同一阈值)."""
    out = format_text("> \u56de\u590d @u\uff1a\n> hi\n\nbody")
    assert out.startswith("<blockquote>")
    assert "<details>" not in out


def test_quote_and_body_fold_independently():
    """引用块与正文各自按同一阈值折叠, 互不外包 (总长控制在截断线内)."""
    out = format_text("> \u56de\u590d @u\uff1a\n> " + "q" * 400 + "\n\n" + "b" * 400)
    assert out.count("<blockquote expandable>") == 1   # 引用块: 整块折
    assert out.count("<details>") == 1                 # 正文: 预览 + 折起
    assert "......" not in out  # 未触发截断


def test_quote_not_folded_when_expandable_disabled():
    out = format_text("> " + "x" * 600, allow_expandable=False)
    assert "<details>" not in out
    assert "<blockquote>" in out


def test_format_text_folds_long_plain_text():
    """折叠 = 开头留预览 + 其余折进 details (收起时只显示摘要, 不留预览就看不到内容)"""
    out = format_text("y" * 600)
    assert out.startswith("y" * 100)                     # 预览在外
    assert "<details><summary>\u5c55\u5f00\u5168\u6587</summary>" in out
    assert out.endswith("</details>")
    assert "y" * 500 in out                              # 折起的部分一个字没丢
    assert "......" not in out


def test_format_text_keeps_short_text_plain():
    assert format_text("short") == "short"


def test_folded_body_keeps_paragraph_breaks():
    """折叠块内**必须保留段落空行**。

    这是换掉 <blockquote expandable> 的原因: 它一遇到块内空行就被 Telegram
    退化成普通引用块 (完全不折叠), 而推文正文天然多段落 —— 长正文于是永远折
    不起来; 而且引用块内的换行会被并成空格, 段落结构全丢。<details> 两者都没
    这个问题。
    """
    body = "\n\n".join(f"第{i}段内容" * 20 for i in range(4))
    out = format_text(body)
    assert "<details>" in out
    assert out.endswith("</details>")
    assert "\n\n第1段" in out        # 段落空行原样保留
    assert out.count("\n\n") >= 4


def test_folded_body_leaves_a_visible_preview():
    """折叠块收起时只显示摘要, 所以开头必须留在外面 —— 否则一个字都看不到"""
    body = "\n\n".join(["开头这一段要能看见", "中间内容" * 40, "结尾内容" * 40])
    out = format_text(body)
    assert out.startswith("开头这一段要能看见")     # 预览在折叠块外
    assert "<details>" in out
    assert out.index("开头这一段要能看见") < out.index("<details>")


def test_single_line_long_text_still_folds():
    """整条正文只有一行时也要折: 行切不出来就按字符切, 否则永远折不起来"""
    from plugins.helpers import split_fold_preview

    preview, rest = split_fold_preview("z" * 500)
    assert preview and rest
    assert preview + rest == "z" * 500
    assert "z" * 400 in rest                   # 剩余足够多, 折了才有意义


def test_split_fold_preview_returns_unfolded_for_short_content():
    """内容太短没有可折的剩余: 返回 (content, "") 让调用方跳过折叠"""
    from plugins.helpers import split_fold_preview

    assert split_fold_preview("很短") == ("很短", "")


def test_long_quote_block_is_folded():
    """被回复/被引用的长卡片也要折 —— 以前它们直接拼进 parts, 1294 字的回复块整屏铺开。

    引用块用**老折叠**（整体一个 <blockquote expandable>），不是正文那套 details：
    后者会把「预览 / 按钮 / 折起部分」切成三段，用户反馈「引用块被按钮分割,
    割裂感太强了」。块内换行必须写成 <br> —— 真换行会被并成空格，真空行则让
    expandable 退化成普通引用块（完全不折叠）。
    """
    from plugins.helpers import fold_quote_block

    quote = "\n".join(["> <i>作者 @handle：</i>", *[f"> <i>第{i}行内容</i>  " for i in range(1, 20)]])
    out = fold_quote_block(quote, summary="展开全文")
    assert out.startswith("<blockquote expandable>")
    assert out.endswith("</blockquote>")
    assert "<details>" not in out                       # 不用 details, 避免割裂
    assert "<i>作者 @handle：</i>" in out                # 斜体走 HTML 标签 (块内 markdown 不解析)
    assert "<i>第19行内容</i>" in out
    assert "*" not in out                               # 不留字面星号
    assert out.count("<br>") == 19                      # 换行走 <br>
    assert "> " not in out                              # '>' 前缀已剥掉


def test_quote_style_comes_from_the_source_not_a_conversion():
    """斜体由 parsehub 的 format_quote_block 直接产出 <i> —— 渲染层不做 *→<i> 转换

    (块内 markdown 行内语法不解析, 写 * 会字面显示星号, 所以源头就得是 <i>)
    """
    from parsehub.utils.helpers import format_quote_block

    assert format_quote_block("斜体行") == "> <i>斜体行</i>\n\n"
    assert format_quote_block("两段\n\n二") == "> <i>两段</i>\n>\n> <i>二</i>\n\n"
    # 作者行也在块内, 同样走 <i>
    assert format_quote_block("正文", "作者") == "> <i>作者：</i>\n> <i>正文</i>\n\n"


def test_unfolded_quote_passes_through_unchanged():
    """不折叠的引用块原样透传 (样式已在源头写好, 渲染层不该再动它)"""
    from plugins.helpers import fold_quote_block

    quote = "> <i>作者：</i>\n> 短内容"
    assert fold_quote_block(quote, summary="展开全文") == quote


def test_short_quote_block_is_not_folded():
    """短引用块不折叠"""
    from plugins.helpers import fold_quote_block

    out = fold_quote_block("> <i>作者：</i>\n> 短内容", summary="展开全文")
    assert "<blockquote expandable>" not in out
    assert "<details>" not in out
    assert out.startswith("> ")


def test_media_goes_inside_a_short_quote():
    """不折叠的引用块: 媒体留在块内 (普通 blockquote 里 ![]() 能正常出图)"""
    from plugins.helpers import render_quote_card

    quote = "> <i>作者：</i>\n> <i>短内容</i>"
    parts = render_quote_card(quote, ["![](tg://photo?id=m0)"], summary="展开全文")
    assert len(parts) == 1
    assert "> ![](tg://photo?id=m0)" in parts[0]        # 在引用块内 (带 > 前缀)
    assert "<blockquote expandable>" not in parts[0]


def test_media_moves_outside_a_folded_quote():
    """**折叠**的引用块: 媒体必须放块外。

    实测折叠块内 `![]()` 不解析 (原样显示成 `![]()` 加一个链接 = 图片格式坏掉);
    块内改用 `<img>` 能出图但会把块退化成不可折叠的普通引用块。
    """
    from plugins.helpers import render_quote_card

    quote = "\n".join(["> <i>作者：</i>", *[f"> <i>第{i}行内容</i>  " for i in range(1, 20)]])
    parts = render_quote_card(quote, ["![](tg://photo?id=m0)"], summary="展开全文")
    assert len(parts) == 2
    folded, media_part = parts
    assert folded.startswith("<blockquote expandable>")     # 文字折起来
    assert "![](tg://" not in folded                        # 折叠块内没有媒体占位符
    assert media_part == "![](tg://photo?id=m0)"            # 媒体独立成段 (块外)
    assert not media_part.startswith(">")


def test_no_media_means_a_single_part():
    from plugins.helpers import render_quote_card

    quote = "\n".join(["> <i>作者：</i>", *[f"> <i>第{i}行内容</i>  " for i in range(1, 20)]])
    assert len(render_quote_card(quote, [], summary="展开全文")) == 1


def test_build_rich_markdown_folds_a_long_reply_block():
    """端到端: 长回复块在完整渲染里被折起来"""
    import types

    from plugins.helpers import build_rich_markdown

    reply = "\n".join(["> <i>Vincent @VincentBounce：</i>", *[f"> <i>第{i}行</i>  " for i in range(1, 18)]])
    content = reply + "\n\n短正文"
    result = types.SimpleNamespace(
        title="", content=content, raw_url="https://x.com/a/status/1",
        author_name="@VincentBounce", author_handle="", author_url="",
        published_at=None, view_count=None, like_count=None, tags=None,
        platform=None, media=None, markdown_content=content,
    )
    config = types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)
    md = build_rich_markdown(result, config=config, lang="zh-hans")
    assert "<blockquote expandable>" in md
    assert "<details>" not in md                 # 引用块不切成三段
    assert "<i>第1行</i>" in md and "<i>第17行</i>" in md


def test_fold_summary_follows_locale():
    """摘要文案由调用方按 locale 传入, 不写死在渲染层"""
    out = format_text("y" * 600, fold_summary="Show full text")
    assert "<summary>Show full text</summary>" in out


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
    assert "<details>" in md
    assert "很长的正文" in md


def test_build_rich_markdown_does_not_truncate_a_very_long_body():
    """富文本没有 caption 的 1024 限制: 超长正文只折叠、不截断。

    以前格式化统一走 1000 字符截断, 长正文被砍成省略号 —— 折叠就轮不上了。
    """
    import types

    from plugins.helpers import build_rich_markdown

    result = types.SimpleNamespace(
        title="",
        content="长" * 1500,
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
    assert "<details>" in md
    assert "......" not in md
    assert "长" * 1400 in md             # 折起的部分完整 (预览 + 折起 = 全文)


def test_format_text_does_not_truncate_by_default():
    """默认**不截断**: 忘了传 max_length 时行为是安全的 (完整渲染), 不是静默砍内容"""
    from plugins.helpers import format_text

    out = format_text("x" * 1200)
    assert "......" not in out
    assert "x" * 1100 in out


def test_caption_path_truncates_only_when_asked():
    """发文件的路径 (send_raw/send_zip) 显式传上限 —— Telegram 媒体 caption 上限 1024"""
    from plugins.helpers import format_text

    out = format_text("x" * 1200, max_length=1000)
    assert "......" in out
    assert len(out) < 1000


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
    """要求截断时, 截断发生在 HTML 转换之前 —— 否则会切断 blockquote 闭合标签"""
    out = format_text("> " + "z" * 1500, max_length=1000)
    assert "......" in out
    assert out.endswith("</blockquote>")    # 截断没有切断闭合标签
    assert out.count("<blockquote expandable>") == 1


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
