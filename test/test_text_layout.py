"""正文排版变换: 硬换行 / 行首 #标签 / 平台标签页。"""

import types

from parsehub.types import ImageParseResult, Platform

from plugins.helpers import link_leading_hashtags, preserve_linebreaks, tag_page_url


def _cfg():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)


def test_preserve_linebreaks_adds_hard_breaks():
    """富文本会吞掉单换行, 行尾要补两个空格"""
    assert preserve_linebreaks("行一\n行二\n行三") == "行一  \n行二  \n行三"


def test_preserve_linebreaks_skips_blank_and_last_lines():
    assert preserve_linebreaks("行一\n\n行三") == "行一\n\n行三"


def test_link_leading_hashtags_uses_tag_page():
    out = link_leading_hashtags("#敬請準時收看", Platform.THREADS)
    assert out.startswith('<a href="https://www.threads.com/search?q=%23')
    assert out.endswith(">\\#敬請準時收看</a>")


def test_link_leading_hashtags_escapes_without_tag_page():
    """平台没有标签页时只转义: 仍然不能让它变标题"""
    assert link_leading_hashtags("#手机", Platform.COOLAPK) == "\\#手机"


def test_link_leading_hashtags_keeps_real_headings():
    """带空格的 # 标题是 Markdown 标题, 不能动"""
    assert link_leading_hashtags("# 标题") == "# 标题"


def test_link_leading_hashtags_only_at_line_start():
    assert link_leading_hashtags("正文 #标签") == "正文 #标签"


def test_threads_body_keeps_its_line_layout():
    """threads 一行一条的排版不能被压成一整段, 末尾 #标签 不能变标题"""
    result = ImageParseResult(content="18:00　動畫\n19:00　動畫二\n#敬請準時收看", photo=[])
    result.platform = Platform.THREADS
    result.raw_url = "https://www.threads.com/@a/post/x"

    from plugins.helpers import build_rich_markdown

    markdown = build_rich_markdown(result, config=_cfg())
    assert "18:00　動畫  \n19:00　動畫二" in markdown
    # 行首不能是裸 "#", 否则被当一级标题 (字号巨大); 标签页链接里的 \# 是转义过的
    assert not any(
        line.lstrip().startswith("#") for line in markdown.splitlines() if "敬請" in line
    )
    assert '<a href="https://www.threads.com/search?q=%23' in markdown


def test_long_tag_line_is_truncated():
    """标签多时截断, 不占满整行 (pixiv 常常 8+ 个标签)"""
    from parsehub.types import ImageParseResult

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.tags = [
        "AI画像",
        "足フェチ",
        "足裏",
        "足の裏",
        "時々ボソッとロシア語でデレる隣のアーリャさん",
        "アリサ・ミハイロヴナ・九条",
        "マリヤ・ミハイロヴナ・九条",
        "周防有希",
    ]

    from plugins.helpers import TAG_LINE_DISPLAY_BUDGET, _display_width, format_tags

    line = format_tags(result)
    assert line.endswith("…")
    # 宽度按可见文本算 (line 里还有 <a href> 标签)
    import re as _re

    visible = _re.sub(r"<[^>]+>", "", line).replace("\\#", "#")
    assert _display_width(visible) <= TAG_LINE_DISPLAY_BUDGET + 3  # 省略号与空格余量
    assert "AI画像" in line
    assert "周防有希" not in line  # 末尾的被省略


def test_short_tag_line_is_kept_whole():
    """标签少时全列, 不加省略号"""
    from parsehub.types import ImageParseResult

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.tags = ["AI画像", "足裏"]

    from plugins.helpers import format_tags

    line = format_tags(result)
    assert "…" not in line
    assert "AI画像" in line and "足裏" in line


def test_single_overlong_tag_is_still_rendered():
    """只有一个标签且超预算时也要出 (别把整行吞成空)"""
    from parsehub.types import ImageParseResult

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.tags = ["非常非常非常非常非常非常非常非常非常非常长的标签名称在这里"]

    from plugins.helpers import format_tags

    line = format_tags(result)
    assert "非常非常" in line
    assert "<a href=" in line


def test_tag_page_url_platforms():
    assert tag_page_url(Platform.PIXIV, "足裏").startswith("https://www.pixiv.net/tags/")
    assert tag_page_url(Platform.COOLAPK, "x") == ""
    assert tag_page_url(None, "x") == ""
