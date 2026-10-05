"""作者行与正文之间的分割线。

用户要求「作者下面的分割线」—— 让"谁发的"与"发了什么"之间有一道界，
与**内容与页脚之间**那条 (``---``) 同一形态。

三个边界都要守住，否则会多出没要求的线: 没有作者行时不插、作者行后面没有内容时不插、
手动折叠时分割线要留在 details **外面** (它分隔的是元信息与内容, 不是内容的一部分)。
"""

import types

from plugins.helpers import build_rich_markdown


def _result(**kwargs):
    fields = {
        "title": "",
        "content": "正文内容",
        "raw_url": "https://x.com/a/status/1",
        "author_name": "作者",
        "author_handle": "handle",
        "author_url": "https://x.com/handle",
        "published_at": None,
        "view_count": None,
        "like_count": None,
        "tags": None,
        "platform": None,
        "media": None,
        "markdown_content": "正文内容",
    }
    fields.update(kwargs)
    return types.SimpleNamespace(**fields)


def _config():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)


def _render(*, spoiler_tag: str = "", **kwargs) -> str:
    return build_rich_markdown(
        _result(**kwargs), config=_config(), lang="zh-hans", hide_content=spoiler_tag
    )


def test_a_divider_sits_between_the_author_line_and_the_body():
    markdown = _render()
    lines = [ln for ln in markdown.splitlines() if ln.strip()]
    assert lines[0].startswith("**")           # 作者行在最上
    assert lines[1] == "---"                   # 紧接着就是分割线
    assert "正文内容" in lines[2]


def test_the_divider_comes_after_the_title_and_author():
    markdown = _render(title="标题")
    lines = [ln for ln in markdown.splitlines() if ln.strip()]
    assert lines[0] == "### 标题"
    assert lines[1].startswith("**")           # 作者行
    assert lines[2] == "---"                   # 分割线在两者之后
    assert "正文内容" in lines[3]


def test_no_divider_without_an_author_line():
    """没有作者行时不插 —— 用户要的是"作者下面的"分割线

    全文只有页脚前那条线 (既有行为), 不能多出一条。
    """
    markdown = _render(author_name="", author_handle="", author_url="")
    assert markdown.count("\n---\n") == 1, markdown
    assert markdown.startswith("正文内容")


def test_no_divider_when_there_is_nothing_after_it():
    """只有作者行、没有内容时不插 (没有要分隔的东西)"""
    markdown = _render(content="", markdown_content="")
    assert markdown.count("\n---\n") == 1, markdown   # 只剩页脚前那条


def test_the_divider_stays_outside_a_folded_body():
    """手动折叠时分割线留在 details **外面** —— 它分隔元信息与内容,

    折进去会变成"折叠块内部的一条线", 起不到分隔作用。
    """
    markdown = _render(spoiler_tag="#nsfw")
    head, rest = markdown.split("<details>", 1)
    inner = rest.split("</details>", 1)[0]
    assert "---" in head, "分割线应在折叠块之外"
    assert "---" not in inner
