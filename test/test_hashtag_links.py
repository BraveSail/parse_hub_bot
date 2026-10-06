"""正文里的 ``#标签`` 一律自己链接化 —— 不靠服务端识别。

背景（2026-10-06, 用户报「为什么有的 tag 是超链接有的不是」）: 标签有两个来源 ——
**标签行**（`format_tags`，我们手动写成链接）与**正文里的裸标签**（靠服务端 hashtag
自动识别）。而服务端的识别有字符限制（实测）:

| 标签 | 服务端识别 | 说明 |
| --- | --- | --- |
| `#INMUKING` / `#アニメ` / `#foo_bar` | ✅ | 正常 |
| `#foo-bar` | ⚠️ 只识别 `#foo` | 连字符处**截断**，看着像标签被切了 |
| `#foo.bar` | ⚠️ 只识别 `#foo` | 点处截断 |
| `#123` | ❌ 完全不识别 | 纯数字标签不可点 |

⇒ 同一条消息里就出现"有的标签是链接、有的不是"。现在正文标签也由我们自己写成
`<a href="平台标签页">`，与标签行同一形态。
"""

from parsehub.types import Platform

from plugins.helpers import link_hashtags


def test_a_tag_in_the_middle_of_a_line_is_linked():
    out = link_hashtags("GPU KING #INMUKING", platform=Platform.TWITTER)
    assert '<a href="https://x.com/hashtag/INMUKING">#INMUKING</a>' in out


def test_a_non_ascii_tag_is_linked_and_url_encoded():
    """日文标签: 服务端能识别, 但我们照样显式链接 (并且 URL 要编码)"""
    out = link_hashtags("作品 #INMUKINGの刑", platform=Platform.TWITTER)
    assert "<a href=" in out
    assert "%E3%81%AE" in out, out          # の 的 UTF-8 百分号编码
    assert ">#INMUKINGの刑</a>" in out


def test_a_hyphenated_tag_is_linked_whole():
    """`#foo-bar` 服务端只认到 `#foo` —— 我们链接整串"""
    out = link_hashtags("#foo-bar", platform=Platform.TWITTER)
    assert ">#foo-bar</a>" in out
    assert "hashtag/foo-bar" in out


def test_a_numeric_tag_is_linked():
    """纯数字标签服务端完全不识别 —— 我们照样给链接"""
    out = link_hashtags("见 #123", platform=Platform.TWITTER)
    assert '<a href="https://x.com/hashtag/123">#123</a>' in out


def test_a_trailing_period_stays_outside_the_tag():
    """句读是边界: `#tag.` 的句号不属于标签"""
    out = link_hashtags("看这个 #tag.", platform=Platform.TWITTER)
    assert ">#tag</a>." in out


def test_a_fragment_in_a_url_is_not_a_tag():
    """URL 里的 `#片段` 不是标签 (前面是字母/斜杠)"""
    out = link_hashtags("https://x.com/a#b", platform=Platform.TWITTER)
    assert "<a href=" not in out


def test_an_existing_anchor_is_not_wrapped_again():
    """已经是链接的整段跳过 (锚点内的文字不再包第二层)"""
    src = '<a href="https://x.com/hashtag/x">#x</a>'
    assert link_hashtags(src, platform=Platform.TWITTER) == src


def test_a_platform_without_a_tag_page_falls_back_to_text():
    """平台没有标签页模板: 退回纯文本; 行首那个 `#` 要转义 (否则变一级标题)"""
    out = link_hashtags("#tag 在行首", platform=None)
    assert out.startswith("\\#tag"), out
    mid = link_hashtags("正文 #tag 在行中", platform=None)
    assert "#tag" in mid and "<a href=" not in mid
    assert "\\#" not in mid, "行中的 # 不必转义"


def test_the_body_tag_ends_up_linked_in_a_full_render():
    """端到端: 正文里的标签在完整渲染里是链接 (这次修的就是它)"""
    import types

    from plugins.helpers import build_rich_markdown

    result = types.SimpleNamespace(
        title="", content="GPU KING #INMUKINGの刑", raw_url="https://x.com/a/status/1",
        author_name="A", author_handle="a", author_url="", published_at=None,
        view_count=None, like_count=None, tags=None, platform=Platform.TWITTER, media=None,
    )
    config = types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)
    md = build_rich_markdown(result, config=config, lang="zh-hans")
    assert '<a href="https://x.com/hashtag/' in md, md
    assert ">#INMUKINGの刑</a>" in md


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
