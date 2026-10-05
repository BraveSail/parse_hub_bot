"""公共排版 helper: 作者链接 / 引用块 / 主页地址 (所有平台共用同一套)."""

from parsehub.types import Platform
from parsehub.utils.helpers import format_author_link, format_quote_block, profile_url


def test_profile_url_per_platform():
    assert profile_url(Platform.TWITTER, "ysoke145") == "https://x.com/ysoke145"
    assert profile_url(Platform.THREADS, "@ani.gamer.com.tw") == "https://www.threads.com/@ani.gamer.com.tw"
    assert profile_url(Platform.PIXIV, user_id="123568955") == "https://www.pixiv.net/users/123568955"


def test_profile_url_returns_empty_when_data_missing():
    """缺所需字段或平台没模板时给空串, 由调用方退回纯文本, 不拼出半截链接"""
    assert profile_url(Platform.PIXIV, "only_handle") == ""
    assert profile_url(Platform.TWITTER, "") == ""
    assert profile_url(None, "someone") == ""
    assert profile_url(Platform.COOLAPK, "someone") == ""


def test_profile_url_escapes_handle():
    assert profile_url(Platform.TWITTER, "a b") == "https://x.com/a%20b"


def test_format_author_link_wraps_handle():
    link = format_author_link("Jason Lee", "huacnlee", "https://x.com/huacnlee")
    assert link == '<a href="https://x.com/huacnlee">Jason Lee</a> <sub><code>@huacnlee</code></sub>'


def test_format_author_link_without_url_is_plain():
    assert format_author_link("隣人X", "user_ydyj5227") == "隣人X @user_ydyj5227"
    assert format_author_link("隣人X", "user_ydyj5227", "") == "隣人X @user_ydyj5227"


def test_format_author_link_collapses_same_name():
    """显示名与用户名相同时只出 @用户名 (链接态也一样)"""
    assert format_author_link("same", "same", "https://x.com/same") == '<a href="https://x.com/same">@same</a>'


def test_format_quote_block_is_italic_and_labelless():
    """引用块不写 引用/回复 字样, 整块斜体, 作者行在前"""
    block = format_quote_block("line1\nline2", 'Jason Lee <a href="u">@h</a>')
    assert block == '> <i>Jason Lee <a href="u">@h</a></i>\n> <i>line1</i>\n> <i>line2</i>\n\n'
    assert "引用" not in block
    assert "回复" not in block


def test_format_quote_block_keeps_blank_lines():
    assert format_quote_block("a\n\nb") == "> <i>a</i>\n>\n> <i>b</i>\n\n"


def test_format_quote_block_without_author():
    assert format_quote_block("x") == "> <i>x</i>\n\n"


def test_format_quote_block_empty_text():
    """默认: 没文字就不出块 —— 没有媒体配套的调用方 (threads) 拿到孤立署名行是噪音"""
    assert format_quote_block("") == ""
    assert format_quote_block("   ") == ""
    assert format_quote_block("", "author") == ""


def test_format_quote_block_sign_only_keeps_the_author_line():
    """sign_only: 只署名也出块 —— 给"被引用对象是纯图/无文字但有媒体"的调用方用"""
    assert format_quote_block("", "author", sign_only=True) == "> <i>author</i>\n\n"
    # 有文字时与普通调用一致
    assert format_quote_block("正文", "author", sign_only=True) == format_quote_block("正文", "author")
    # 连作者都没有 -> 仍然是空串 (sign_only 也救不了)
    assert format_quote_block("", "", sign_only=True) == ""
