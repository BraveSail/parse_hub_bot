"""作者行: 有 handle 挂在 handle 上, 没有 handle 就挂在名字上。"""

import types

from parsehub.types import ImageParseResult, Platform
from parsehub.utils.helpers import format_author_link

from plugins.helpers import format_author_line


def _result(**kwargs):
    result = ImageParseResult(content="正文", photo=[])
    for key, value in kwargs.items():
        setattr(result, key, value)
    return result


def test_name_is_the_link_and_handle_is_a_subscript():
    """作者行: **显示名做成链接**, ``@handle`` 是**等宽下角标** (用户要求)。

    两处都不能少: ``<code>`` 让 ``@handle`` 不再是 Mention (否则仍可点),
    ``<sub>`` 把它压成下角标; 两个名字之间空一格。
    """
    out = format_author_link("言吾言_", "yanwuyan", "https://x.com/yanwuyan")
    assert out == '<a href="https://x.com/yanwuyan">言吾言_</a> <sub><code>@yanwuyan</code></sub>'


def test_name_is_the_link_when_there_is_no_handle():
    """B 站这类只有主页 ID 的平台: 链接要挂在显示名上, 否则整行不可点"""
    out = format_author_link("言吾言_", "", "https://space.bilibili.com/12345")
    assert out == '<a href="https://space.bilibili.com/12345">言吾言_</a>'


def test_no_url_means_plain_label():
    assert format_author_link("某人", "someone", "") == "某人 @someone"


def test_identical_name_and_handle_keeps_the_handle_linked():
    out = format_author_link("someone", "someone", "https://x.com/someone")
    assert out == '<a href="https://x.com/someone">@someone</a>'


def test_author_line_links_bilibili_name():
    result = _result(
        author_name="言吾言_",
        author_handle="",
        author_url="https://space.bilibili.com/12345",
        platform=Platform.BILIBILI,
    )
    assert format_author_line(result) == '**<a href="https://space.bilibili.com/12345">言吾言_</a>**'


def test_author_line_without_url_is_plain():
    result = _result(author_name="某人", author_handle="", author_url="")
    assert format_author_line(result) == "**某人**"


def test_author_line_is_empty_without_author():
    assert format_author_line(types.SimpleNamespace(author_name="", author_handle="", author_url="")) == ""
