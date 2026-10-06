"""长帖的折叠形态: **整篇只折一次**, 分隔线要被认成分隔线。

用户报障原话: 「问题很大, 被discourse的格式影响了, 很多折叠按钮, 还有个显示更多」。
真机取证 (一篇 linux.do 长帖): 渲染出 **7 个 `<details>`** (summary 全是"展开全文"),
外加 **18 处字面 `---` 段落**。根因分别是:

- 折叠: `format_text` 按引用块把正文切成多段, **每段各自判断**是否超阈值 —— discourse 的
  楼层引用动辄十几个, 于是切出十几个片段, 7 个超阈值 → 7 个折叠按钮。
- 字面 `---`: 原文里 `---` 紧贴上一行, 服务端要前后有空行才认它是分隔线。
"""

from plugins.helpers import _FOLD_CHAR_THRESHOLD, format_text

QUOTE = "> <i><a href=\"https://linux.do/u/someone\">@someone</a></i> 这是一段引用内容, 长度普通。"


def _post(quote_count: int = 12, *, filler: int = 0) -> str:
    """模拟 discourse 长帖: 一堆楼层引用夹在正文之间。"""
    blocks = []
    for i in range(quote_count):
        blocks.append(f"## 小节 {i}\n\n这是第 {i} 段正文内容。" + "补充文字" * filler)
        blocks.append(QUOTE)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- 折叠: 整篇一次


def test_a_quote_heavy_post_folds_exactly_once():
    """**核心**: 引用多不能变成多个折叠按钮 (用户的抱怨就是这个)"""
    out = format_text(_post(12), fold_summary="展开全文")
    assert out.count("<details>") == 1, out.count("<details>")
    assert out.count("</details>") == 1


def test_the_whole_post_is_covered_by_that_single_fold():
    """折叠覆盖整篇: 所有小节要么在预览里、要么在 details 里, 不会丢在外面散着。

    (折叠的**对象**是整篇; 预览只是它开头的几行, 所以首节会在 details 之外。)
    """
    out = format_text(_post(12), fold_summary="展开全文")
    assert out.count("<details>") == 1
    for i in (0, 11):
        assert f"小节 {i}" in out, f"第 {i} 节不见了"
    head, _, tail = out.partition("<details>")
    assert "小节 11" in tail, "靠后的小节必须在折叠里"
    assert "小节 11" not in head


def test_the_fold_keeps_the_first_lines_as_a_preview():
    """折叠后**开头几行留在外面**当预览。

    (曾一度全收起 —— 结果用户报「没有前几行」; 收起时客户端只显示 summary,
    不留预览就看不到一点内容。)
    """
    out = format_text(_post(12), fold_summary="展开全文")
    head, rest = out.split("<details>", 1)
    assert head.strip(), f"展开按钮之前必须有预览行: {out[:120]!r}"
    assert "<details>" in out                                   # 仍然只折一次
    assert out.count("<details>") == 1
    # 预览是被折内容的前面部分, 且剩余部分确实在 details 里
    preview_lines = [ln for ln in head.strip().splitlines() if ln.strip()]
    assert 1 <= len(preview_lines) <= 3, preview_lines


def test_short_content_is_not_folded():
    """没超阈值就不折 (阈值 500 字符 / 8 非空行)"""
    out = format_text("短正文 **粗体**\n\n> 一条引用", fold_summary="展开全文")
    assert "<details>" not in out
    assert len(out) < _FOLD_CHAR_THRESHOLD


def test_folding_does_not_nest():
    """**不做嵌套折叠**: 整篇折了之后, 内部引用块不能再自己折一次"""
    out = format_text(_post(12), fold_summary="展开全文")
    inner = out.split("<details>", 1)[1]
    assert "<blockquote expandable>" not in inner
    assert "<blockquote>" in inner  # 引用块本身还在, 只是不可展开


def test_a_single_long_quote_folds_the_message_not_itself():
    """**行为变化**: 一条超长引用块以前会自己折成可展开引用块, 现在整篇折一次。

    (引用块的长度算在整篇判断里, 所以"整篇没超、引用块却超"不可能出现;
    折叠点只有"整篇"这一个, 也就不会有嵌套折叠。)
    """
    long_quote = "> " + "引用内容很长。" * 100  # 700 字符, 单块就超阈值
    out = format_text(long_quote, fold_summary="展开全文")
    assert out.count("<details>") == 1
    assert "<blockquote expandable>" not in out


def test_a_quote_that_fits_does_not_fold_anything():
    """整篇没超阈值: 不折 (引用块也不再自己折)"""
    out = format_text("短正文\n\n> 一条普通引用", fold_summary="展开全文")
    assert "<details>" not in out
    assert "<blockquote expandable>" not in out
    assert "<blockquote>" in out


def test_folding_can_be_disabled():
    """allow_expandable=False 的通道 (inline 等) 不折"""
    out = format_text(_post(12), allow_expandable=False, fold_summary="展开全文")
    assert "<details>" not in out


def test_the_summary_is_the_translated_one():
    out = format_text(_post(12), fold_summary="Show more")
    assert "<summary>Show more</summary>" in out


# ---------------------------------------------------------------- 分隔线


def test_a_hr_glued_to_the_previous_line_gets_blank_lines_around_it():
    """**核心**: 原文里 `---` 紧贴上一行 (discourse 常见), 要补空行让它成为分隔线。

    不补的话服务端把它当普通文本 (实测 18 处字面 '---' 段落); 若紧跟在文字行后面
    还会被当 setext 标题语法, 把上一行整行变成 H2。
    """
    out = format_text("1. 列表项\n---\n# 标题", fold_summary="展开全文")
    assert "\n\n---\n\n" in out
    assert "列表项\n---" not in out


def test_a_hr_after_text_does_not_swallow_the_line_above():
    """紧跟在文字行后的 `---`: 上一行不能被 setext 当成标题"""
    out = format_text("这是正文一行\n---\n后面还有", fold_summary="展开全文")
    lines = out.splitlines()
    assert lines[lines.index("---") - 1] == ""
    assert "这是正文一行" in lines
    assert lines[lines.index("---") - 2] == "这是正文一行"


def test_longer_and_alternative_hr_forms_are_normalised():
    for marker in ("---", "----", "***", "___"):
        out = format_text(f"正文\n{marker}\n后文", fold_summary="展开全文")
        assert "\n\n---\n\n" in out, marker


def test_bold_and_italic_are_not_mistaken_for_a_hr():
    """``**粗体**`` / ``***粗斜体***`` 不是独占一行的纯分隔符, 不能被改写成分隔线"""
    out = format_text("**粗体**\n\n***粗斜体***\n\n普通文字", fold_summary="展开全文")
    assert "**粗体**" in out
    assert "***粗斜体***" in out
    assert "---" not in out


def test_a_table_separator_is_not_touched():
    """表格的分隔行 (``|---|---|``) 不是独立分隔线"""
    out = format_text("| a | b |\n|---|---|\n| 1 | 2 |", fold_summary="展开全文")
    assert "|---|---|" in out


def test_hr_is_normalised_inside_the_fold_too():
    """折叠块内部同样要补空行 (实测字面 '---' 正是出现在 details 内部)"""
    out = format_text(_post(12) + "\n---\n尾部", fold_summary="展开全文")
    inner = out.split("<details>", 1)[1]
    assert "\n\n---\n\n" in inner


def test_repeated_hr_does_not_leave_a_hole():
    """连续的 `---` 不该撑出一大段空白 (合并多余空行)"""
    out = format_text("正文\n---\n---\n后文", fold_summary="展开全文")
    assert "\n\n\n\n" not in out
    assert out.count("---") == 2
