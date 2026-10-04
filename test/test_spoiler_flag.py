"""手动折叠开关: 链接后跟独立的 hashtag 标记。

用户要求：把 ``/s`` 换成 ``#nsfw`` / ``#spoiler``（判定方式不变），
摘要去掉原来的文字、换成 ``⚠️ <标记>``；随后再加 ``#r18``（不分大小写）、
``#劇透``、``#色色``。

**为什么用空格分隔的独立标记**（而不是 URL 参数或路径后缀）：
- URL 参数会被 ``get_raw_url`` 的清理逻辑摘掉；
- 路径后缀会污染缓存 key，且部分平台的 provider 按固定段数解析路径会出错；
- 独立 token 让 URL 本体完全不变，且纯链接判定剔掉它之后不受影响。

所有标记**等价**（都触发遮挡），区别只在语义 —— 摘要显示用户自己写的那个，
让读者知道为什么藏起来（不宜公开 / 剧透），所以解析函数返回**标记本身**而非 bool。
匹配大小写不敏感，但返回值保持用户原形。
"""

import types

from parsehub.utils.helpers import SPOILER_FLAGS, strip_spoiler_flag, url_only_message_urls

from plugins.helpers import build_rich_markdown

LINK = "https://x.com/a/status/123"


# ── 标记解析 ───────────────────────────────────────────────────────────


def test_every_flag_is_recognised():
    assert SPOILER_FLAGS == ("#nsfw", "#spoiler", "#r18", "#劇透", "#剧透", "#色色")
    for flag in SPOILER_FLAGS:
        assert strip_spoiler_flag(f"{LINK} {flag}") == (LINK, flag), flag


def test_flags_are_case_insensitive():
    """``#R18`` / ``#NSFW`` 这些大写写法同样算 —— 返回的是用户写的原形"""
    assert strip_spoiler_flag(f"{LINK} #R18") == (LINK, "#R18")
    assert strip_spoiler_flag(f"{LINK} #NSFW") == (LINK, "#NSFW")
    assert strip_spoiler_flag(f"{LINK} #Spoiler") == (LINK, "#Spoiler")


def test_flag_works_at_the_front_and_with_extra_spaces():
    assert strip_spoiler_flag(f"#nsfw {LINK}") == (LINK, "#nsfw")
    assert strip_spoiler_flag(f"{LINK}    #spoiler") == (LINK, "#spoiler")


def test_no_flag_means_nothing_changes():
    assert strip_spoiler_flag(LINK) == (LINK, "")
    assert strip_spoiler_flag("") == ("", "")


def test_flag_must_be_a_standalone_token():
    """``#nsfwxx`` 不是开关; URL 里的片段也不是（判定方式与 ``/s`` 时期一致）"""
    assert strip_spoiler_flag(f"{LINK} #nsfwxx") == (f"{LINK} #nsfwxx", "")
    assert strip_spoiler_flag("https://x.com/nsfw/status/1") == ("https://x.com/nsfw/status/1", "")
    assert strip_spoiler_flag(f"{LINK} #r18x") == (f"{LINK} #r18x", "")


def test_first_flag_wins_when_both_are_given():
    """两个都写时以先出现的为准 —— 摘要要显示哪一个不能随缘"""
    assert strip_spoiler_flag(f"{LINK} #nsfw #spoiler") == (LINK, "#nsfw")
    assert strip_spoiler_flag(f"{LINK} #spoiler #nsfw") == (LINK, "#spoiler")


# ── 与"纯链接"判定共存 ─────────────────────────────────────────────────


def test_flag_does_not_break_the_url_only_check():
    """``<链接> <标记>`` 仍要算纯链接消息 —— 否则群里/guest 根本不会触发"""
    for flag in SPOILER_FLAGS:
        assert url_only_message_urls(f"{LINK} {flag}") == [LINK], flag
    assert url_only_message_urls(LINK) == [LINK]


def test_other_text_still_disqualifies():
    """开关之外还夹着别的文字 -> 仍不是纯链接"""
    assert url_only_message_urls(f"{LINK} #nsfw 看这个") == []


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


def test_summary_shows_the_flag_the_user_wrote():
    """摘要 = ⚠️ + 用户写的标记；不再写「展开全文」"""
    for tag in (*SPOILER_FLAGS, "#R18"):
        md = build_rich_markdown(_result("正文"), config=_config(), lang="zh-hans", hide_content=tag)
        assert f"<details><summary>⚠️ {tag}</summary>" in md, tag


def test_summary_no_longer_carries_the_fold_label():
    md = build_rich_markdown(_result("正文"), config=_config(), lang="zh-hans", hide_content="#nsfw")
    assert "展开全文" not in md


def test_summary_does_not_depend_on_the_language():
    """标记是用户输入, 不翻译 —— 任何语言下都原样显示"""
    for lang in ("zh-hans", "en-us", "ja-jp"):
        md = build_rich_markdown(_result("正文"), config=_config(), lang=lang, hide_content="#nsfw")
        assert "<summary>⚠️ #nsfw</summary>" in md


def test_hide_content_leaves_no_preview():
    """与自动折叠相反: 手动遮住时开头**不留预览**, 否则等于没遮"""
    body = "开头这一句不该露出来\n\n后面的内容"
    md = build_rich_markdown(_result(body), config=_config(), lang="zh-hans", hide_content="#nsfw")
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
    md = build_rich_markdown(_result(content), config=_config(), lang="zh-hans", hide_content="#nsfw")
    inner = md.split("<details>", 1)[1]
    assert "@楼主" in inner and "本层正文" in inner


def test_media_goes_inside_the_fold():
    """媒体占位符也必须在 details 内 —— 否则图露在外面, 等于没遮"""
    md = build_rich_markdown(
        _result("本层正文"),
        config=_config(),
        lang="zh-hans",
        media_placeholders=["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"],
        hide_content="#spoiler",
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
        hide_content="#nsfw",
    )
    inner = md.split("<details>", 1)[1]
    assert "tg://photo?id=q0" in inner
