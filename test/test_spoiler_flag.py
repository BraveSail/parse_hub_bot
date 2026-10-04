"""手动打码开关: 链接后跟独立的 ``/s``。

用户要求：链接 + 空格 + ``/s`` → 本次结果把正文折成
``<details><summary>⚠️</summary>``（用正文那套折叠，不留预览）。

**为什么用空格分隔的独立标记**（而不是 URL 参数或路径后缀）：
- URL 参数会被 ``get_raw_url`` 的清理逻辑摘掉；
- 路径后缀（``/s``）会污染缓存 key，且部分平台的 provider 按固定段数解析路径会出错；
- 独立 token 让 URL 本体完全不变，且纯链接判定剔掉它之后不受影响。
"""

import types

from parsehub.utils.helpers import strip_spoiler_flag, url_only_message_urls

from plugins.helpers import build_rich_markdown

LINK = "https://x.com/a/status/123"


# ── 标记解析 ───────────────────────────────────────────────────────────


def test_flag_after_the_link_is_recognised():
    assert strip_spoiler_flag(f"{LINK} /s") == (LINK, True)


def test_flag_works_at_the_front_and_with_extra_spaces():
    assert strip_spoiler_flag(f"/s {LINK}") == (LINK, True)
    assert strip_spoiler_flag(f"{LINK}    /s") == (LINK, True)


def test_no_flag_means_nothing_changes():
    assert strip_spoiler_flag(LINK) == (LINK, False)
    assert strip_spoiler_flag("") == ("", False)


def test_flag_must_be_a_standalone_token():
    """``/soccer`` 不是开关; URL 里的 ``/s`` 也不是"""
    assert strip_spoiler_flag(f"{LINK} /soccer") == (f"{LINK} /soccer", False)
    assert strip_spoiler_flag("https://mp.weixin.qq.com/s/abc") == ("https://mp.weixin.qq.com/s/abc", False)


def test_repeated_flags_are_fine():
    assert strip_spoiler_flag(f"{LINK} /s /s") == (LINK, True)


# ── 与"纯链接"判定共存 ─────────────────────────────────────────────────


def test_flag_does_not_break_the_url_only_check():
    """``<链接> /s`` 仍要算纯链接消息 —— 否则群里/guest 根本不会触发"""
    assert url_only_message_urls(f"{LINK} /s") == [LINK]
    assert url_only_message_urls(LINK) == [LINK]


def test_other_text_still_disqualifies():
    """开关之外还夹着别的文字 -> 仍不是纯链接"""
    assert url_only_message_urls(f"{LINK} /s 看这个") == []


# ── 渲染 ───────────────────────────────────────────────────────────────


def _result(content: str):
    return types.SimpleNamespace(
        title="",
        content=content,
        raw_url=LINK,
        author_name="",
        author_handle="",
        author_url="",
        published_at=None,
        view_count=None,
        like_count=None,
        tags=None,
        platform=None,
        media=None,
        markdown_content=content,
    )


def _config():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)


def test_hide_content_folds_the_body_with_a_warning_summary():
    md = build_rich_markdown(_result("第一段\n\n第二段"), config=_config(), lang="zh-hans", hide_content=True)
    assert "<details><summary>⚠️ 展开全文</summary>" in md
    assert "第一段" in md and "第二段" in md


def test_hide_content_leaves_no_preview():
    """与自动折叠相反: 手动遮住时开头**不留预览**, 否则等于没遮"""
    body = "开头这一句不该露出来\n\n后面的内容"
    md = build_rich_markdown(_result(body), config=_config(), lang="zh-hans", hide_content=True)
    folded_at = md.index("<details>")
    assert "开头这一句不该露出来" not in md[:folded_at]


def test_without_the_flag_the_body_stays_normal():
    md = build_rich_markdown(_result("短正文"), config=_config(), lang="zh-hans")
    assert "<details>" not in md
    assert "短正文" in md


def test_hide_content_hides_everything_but_the_header():
    """**所有内容都遮**（用户明确要求）: 正文、引用块、标签、媒体全进 details。

    只有标题与作者留在外面 —— 那是"这是什么"的元信息, 全遮掉会看不出解析了什么。
    """
    content = "> <i>@楼主 · #1：</i>\n\n本层正文"
    md = build_rich_markdown(_result(content), config=_config(), lang="zh-hans", hide_content=True)
    inner = md.split("<details>", 1)[1]
    assert "@楼主" in inner and "本层正文" in inner
    assert "<details>" in md


def test_media_goes_inside_the_fold():
    """媒体占位符也必须在 details 内 —— 否则图露在外面, 等于没遮"""
    content = "本层正文"
    md = build_rich_markdown(
        _result(content),
        config=_config(),
        lang="zh-hans",
        media_placeholders=["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"],
        hide_content=True,
    )
    inner = md.split("<details>", 1)[1]
    outside = md.replace(inner, "")
    assert "tg://photo?id=m0" in inner and "tg://photo?id=m1" in inner
    assert "tg://photo" not in outside


def test_quoted_media_also_goes_inside_the_fold():
    """引用块的媒体同样要遮进去"""
    content = "主推正文\n\n> <i>@被引用者：</i>\n> <i>引用内容</i>"
    md = build_rich_markdown(
        _result(content),
        config=_config(),
        lang="zh-hans",
        quote_media_placeholders=["![](tg://photo?id=q0)"],
        hide_content=True,
    )
    inner = md.split("<details>", 1)[1]
    assert "tg://photo?id=q0" in inner


def test_spoiler_summary_reuses_the_existing_fold_label():
    """摘要 = ⚠️ + **现成的折叠按钮文案**（「展开全文」那个词条, 16 语言早已就位）

    不新建翻译: 折叠按钮的文字本来就有, 另起一个只会多出 16 条要维护的译文。
    """
    content = "正文"
    for lang, expected in (("zh-hans", "展开全文"), ("zh-hant", "展開全文"),
                           ("en-us", "Show full text"), ("ja-jp", "全文を表示")):
        md = build_rich_markdown(_result(content), config=_config(), lang=lang, hide_content=True)
        assert f"<summary>⚠️ {expected}</summary>" in md
