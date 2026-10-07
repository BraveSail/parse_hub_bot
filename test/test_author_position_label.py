"""作者行上的位置标记（楼层号）。

用户报障原话：「https://linux.do/t/topic/2989140/4?u=libc.so.6 主楼标楼层号了但是回复没标」

渲染出来的缺口::

    **<a href=".../u/MystDove">@MystDove</a>**        ← 本层(4楼): 没标
    > <i>…Leo…</i> · #1                                ← 主楼引用块: 标了

⇒ 作者行末尾补 `` · #N``（与引用块**同一形态**）。位置标记在**粗体外面**
（粗体标的是"谁写的"）。

**没有这个字段的平台输出必须逐字不变** —— 这是不能把普通帖子改坏的那条线。
"""

import types

from plugins.helpers import build_rich_markdown, format_author_line


def _result(**kwargs):
    fields = {
        "title": "",
        "content": "正文内容",
        "raw_url": "https://linux.do/t/topic/1",
        "author_name": "MystDove",
        "author_handle": "MystDove",
        "author_url": "https://linux.do/u/MystDove",
        "published_at": None,
        "view_count": None,
        "like_count": None,
        "tags": None,
        "platform": None,
        "is_sensitive": False,
        "quoted_media_count": 0,
        "reply_media_count": 0,
        "position_label": "",
    }
    fields.update(kwargs)
    return types.SimpleNamespace(**fields)


def _config():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)


# ---------------------------------------------------------------- 作者行本身


def test_the_floor_lands_at_the_end_of_the_author_line():
    """**核心**: 有位置标记时接在作者行末尾"""
    line = format_author_line(_result(position_label="#4"))
    assert line.endswith(" · #4"), line
    assert "<a href=" in line


def test_the_floor_is_outside_the_bold():
    """粗体只包名字 —— 位置标记与 @handle 一样**不进粗体**"""
    line = format_author_line(_result(position_label="#4"))
    assert line.endswith("</a>**") is False, line
    assert "**" in line
    # 粗体在 #4 之前就闭合了
    assert line.index("**", line.index("**") + 2) < line.index("#4"), line


def test_no_label_means_a_byte_identical_author_line():
    """**核心回归**: 没有位置标记时, 作者行与改动前逐字一致"""
    assert format_author_line(_result()) == format_author_line(_result(position_label=""))


def test_a_plain_handle_line_also_gets_the_floor():
    """只有 handle 没有主页地址时也带楼层号（那种形态走另一条分支）"""
    line = format_author_line(
        _result(author_url="", author_name="MystDove", author_handle="MystDove", position_label="#9")
    )
    assert line.endswith(" · #9"), line


def test_an_author_line_without_a_name_still_gets_the_floor():
    """没有作者信息时作者行本来就为空 —— 不因为加了楼层号而凭空产生一行"""
    assert format_author_line(_result(author_name="", author_handle="", author_url="")) == ""


# ---------------------------------------------------------------- 整篇渲染


def test_the_rendered_post_carries_the_floor():
    out = build_rich_markdown(
        _result(position_label="#4"), config=_config(), lang="zh-hans"
    )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0].endswith(" · #4"), lines[0]
    # 分割线仍在作者行与正文之间（不能被楼层号挤掉）
    assert lines[1] == "---", lines[1]


def test_other_platforms_render_unchanged():
    """twitter / threads 这类没有位置概念的平台: 渲染结果不含 ` · #`"""
    from parsehub.types import Platform

    for platform in (Platform.TWITTER, Platform.THREADS, Platform.BILIBILI):
        out = build_rich_markdown(_result(platform=platform), config=_config(), lang="zh-hans")
        assert " · #" not in out, platform


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
