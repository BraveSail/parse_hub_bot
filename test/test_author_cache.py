import asyncio
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pyrogram.parser import Parser

from plugins.helpers import build_caption_by_str, build_metadata_line, build_rich_markdown
from services.cache import CacheEntry, CacheParseResult, PersistentCache


@pytest.mark.parametrize(
    ("author", "versioned", "hit"),
    [("", False, False), ("Author", False, True), ("", True, True), ("Author", True, True)],
)
def test_cache_refreshes_only_legacy_entries_missing_author(author, versioned, hit):
    payload = {
        "parse_result": {
            "title": "T",
            "author_name": author,
            "published_at": None,
            "view_count": None,
            "tags": [],
        }
    }
    if versioned:
        payload["author_metadata_version"] = 1
    stored = SimpleNamespace(entry_json=payload)
    repo = SimpleNamespace(get=AsyncMock(return_value=stored), touch=AsyncMock())

    @asynccontextmanager
    async def session():
        yield None

    with patch("services.cache.get_session", session), patch("services.cache.CacheRepo", return_value=repo):
        result = asyncio.run(PersistentCache().get("https://www.threads.com/@user/post/Abc"))
    assert (result is not None) == hit
    assert repo.touch.await_count == int(hit)


def test_cache_refreshes_entries_written_before_metadata_line():
    """没有统计字段的缓存要重新解析, 否则同一链接第二次发送就少了时间/浏览量那行"""
    payload = {"parse_result": {"title": "T", "author_name": "Author", "is_sensitive": False}}
    payload["author_metadata_version"] = 1
    stored = SimpleNamespace(entry_json=payload)
    repo = SimpleNamespace(get=AsyncMock(return_value=stored), touch=AsyncMock())

    @asynccontextmanager
    async def session():
        yield None

    with patch("services.cache.get_session", session), patch("services.cache.CacheRepo", return_value=repo):
        result = asyncio.run(PersistentCache().get("https://www.threads.com/@user/post/Abc"))
    assert result is None
    assert repo.touch.await_count == 0


def test_cache_refreshes_entries_written_before_tags():
    """没有 tags 字段的缓存要重新解析, 否则 inline 结果缺 tag 行"""
    payload = {
        "parse_result": {"title": "T", "author_name": "Author", "published_at": None, "view_count": None},
        "author_metadata_version": 1,
    }
    stored = SimpleNamespace(entry_json=payload)
    repo = SimpleNamespace(get=AsyncMock(return_value=stored), touch=AsyncMock())

    @asynccontextmanager
    async def session():
        yield None

    with patch("services.cache.get_session", session), patch("services.cache.CacheRepo", return_value=repo):
        result = asyncio.run(PersistentCache().get("https://www.pixiv.net/artworks/1"))
    assert result is None
    assert repo.touch.await_count == 0


def test_new_authorless_cache_is_versioned():
    entry = CacheEntry(parse_result=CacheParseResult())
    payload = entry.model_dump(mode="json")
    assert payload["author_metadata_version"] == 1
    assert "author_metadata_version" in CacheEntry.model_validate(payload).model_fields_set


@pytest.mark.parametrize(
    "author",
    ["Author", "A & B <test>", "\u30d6\u30eb\u30fc\u30a2\u30fc\u30ab\u30a4\u30d6\u516c\u5f0f", "Author \U0001f600"],
)
def test_author_is_bold_and_html_escaped(author):
    caption = build_caption_by_str("", "Body", "https://www.threads.com/@user/post/Abc", author_name=author)
    parsed = asyncio.run(Parser(None).parse(caption))
    assert parsed["message"].startswith(author + ":\n\n")
    length = len((author + ":").encode("utf-16-le")) // 2
    assert any(
        type(e).__name__ == "MessageEntityBold" and e.offset == 0 and e.length == length for e in parsed["entities"]
    )


# ── 统计行 ────────────────────────────────────────────────────


def test_metadata_line_zh_format():
    """中文按 X 的观感: 时间 · 中文日期 · 千分位浏览量"""
    line = build_metadata_line(
        published_at=__import__("datetime").datetime(2026, 10, 3, 11, 0, tzinfo=__import__("datetime").UTC),
        view_count=1455,
        lang="zh-hans",
        view_label="查看",
    )
    assert line == "下午7:00 · 2026年10月3日 · 1,455 查看"


def test_metadata_line_other_language_uses_numeric_datetime():
    import datetime

    line = build_metadata_line(
        published_at=datetime.datetime(2026, 10, 3, 11, 0, tzinfo=datetime.UTC),
        view_count=1455,
        lang="en-us",
        view_label="views",
    )
    assert line == "19:00 · 2026-10-03 · 1,455 views"


def test_metadata_line_without_views():
    """threads 拿不到浏览量: 只显示时间, 不能留空占位"""
    import datetime

    line = build_metadata_line(
        published_at=datetime.datetime(2026, 10, 3, 11, 0, tzinfo=datetime.UTC), view_count=None, lang="zh-hans"
    )
    assert line == "下午7:00 · 2026年10月3日"
    assert " ·  · " not in line


def test_metadata_line_empty_when_nothing_available():
    assert build_metadata_line() == ""


def test_caption_places_metadata_before_source():
    caption = build_caption_by_str(
        "Title", "Body", "https://x.com/u/status/1", author_name="Author", metadata_line="下午7:00 · 2026年10月3日"
    )
    assert "Body" in caption
    assert caption.index("2026年10月3日") < caption.index("Source")
    assert caption.index("Author") < caption.index("2026年10月3日")


# ── 富文本正文 ────────────────────────────────────────────────


def test_rich_markdown_puts_metadata_and_source_in_footer():
    """统计与来源都在页尾 footer 里, 正文保持原文排版"""
    from datetime import UTC, datetime

    from parsehub.types import MultimediaParseResult, Platform

    result = MultimediaParseResult(content="# 标题\n\n正文**粗体**\n\n- 列表项")
    result.platform = Platform.TWITTER
    result.raw_url = "https://x.com/u/status/1"
    result.author_name = "Author"
    result.published_at = datetime(2026, 10, 3, 11, 0, tzinfo=UTC)
    result.view_count = 1455

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False),
        lang="zh-hans", view_label="查看",
    )

    assert markdown.startswith("**Author：**")
    assert "正文**粗体**" in markdown
    assert "- 列表项" in markdown
    assert "---" in markdown
    # 统计与来源在 footer, 且位于正文之后
    footer = markdown.split("<footer>")[1]
    assert "下午7:00 · 2026年10月3日 · 1,455 查看" in footer
    # footer 里 markdown 链接语法不生效, 必须用 HTML 的 a href
    assert '<a href="https://x.com/u/status/1">Source（Twitter）</a>' in footer
    assert markdown.index("- 列表项") < markdown.index("<footer>")


def test_rich_markdown_author_label_with_handle():
    """作者名带上用户名: "名字 @handle"; 名字与用户名相同时只写 @handle"""
    from parsehub.types import MultimediaParseResult, Platform

    config = types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)
    result = MultimediaParseResult(content="正文")
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.author_name = "隣人X"
    result.author_handle = "user_ydyj5227"
    assert build_rich_markdown(result, config=config).startswith("**隣人X @user_ydyj5227：**")

    only_handle = MultimediaParseResult(content="正文")
    only_handle.platform = Platform.THREADS
    only_handle.raw_url = "https://www.threads.com/@same/post/x"
    only_handle.author_name = "same"
    only_handle.author_handle = "same"
    assert build_rich_markdown(only_handle, config=config).startswith("**@same：**")


def test_rich_markdown_wraps_multiple_media_in_collage():
    """多张媒体必须包在 <tg-collage> 里才是图集, 否则显示成各自独立的图"""
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="图集", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"

    markdown = build_rich_markdown(
        result,
        config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False),
        media_placeholders=["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"],
    )
    assert "<tg-collage>" in markdown
    assert "</tg-collage>" in markdown
    assert markdown.index("tg://photo?id=m0") < markdown.index("tg://photo?id=m1") < markdown.index("</tg-collage>")


def test_rich_markdown_single_media_is_not_wrapped():
    """单张媒体不需要图集包裹"""
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="单图", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"

    markdown = build_rich_markdown(
        result,
        config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False),
        media_placeholders=["![](tg://photo?id=m0)"],
    )
    assert "<tg-collage>" not in markdown
    assert "tg://photo?id=m0" in markdown


def test_rich_markdown_renders_tags():
    """平台给的标签渲染成 #标签 一行"""
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.tags = ["AI画像", "足裏"]

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)
    )
    # # 要转义 (否则被当一级标题/字号巨大), 并渲染成标签页链接
    assert '<a href="https://www.pixiv.net/tags/AI%E7%94%BB%E5%83%8F">\\#AI画像</a>' in markdown
    assert '<a href="https://www.pixiv.net/tags/%E8%B6%B3%E8%A3%8F">\\#足裏</a>' in markdown
    assert markdown.index("正文") < markdown.index("AI画像")


def test_tags_with_middle_dot_are_linked_not_bare_hashtags():
    """含 ``・`` 的长标签必须走链接。

    裸 hashtag 时 Telegram 遇到 ``・`` 就停止解析, 只染蓝到中点之前,
    看起来就是标签被截断 (实测 ``#アリサ・ミハイロヴナ・九条`` -> ``#アリサ``)。
    """
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"
    result.tags = ["アリサ・ミハイロヴナ・九条"]

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)
    )
    assert "<a href=" in markdown
    assert "アリサ・ミハイロヴナ・九条</a>" in markdown


def test_tags_fall_back_to_plain_text_without_tag_page():
    """平台没有标签页时退回纯文本 #标签"""
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="正文", photo=[])
    result.platform = Platform.COOLAPK
    result.raw_url = "https://www.coolapk.com/feed/1"
    result.tags = ["手机"]

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)
    )
    assert "\\#手机" in markdown
    tag_line = next(line for line in markdown.splitlines() if "手机" in line)
    assert "<a href" not in tag_line


def test_tag_page_url_encodes_japanese():
    from parsehub.types import Platform

    from plugins.helpers import tag_page_url

    assert tag_page_url(Platform.PIXIV, "周防有希") == "https://www.pixiv.net/tags/%E5%91%A8%E9%98%B2%E6%9C%89%E5%B8%8C"
    assert tag_page_url(Platform.XHS, "x") == "" or "xiaohongshu" in tag_page_url(Platform.XHS, "x")
    assert tag_page_url(None, "x") == ""


def test_rich_markdown_places_media_between_body_and_footer():
    from parsehub.types import ImageParseResult, Platform

    result = ImageParseResult(content="图集", photo=[])
    result.platform = Platform.PIXIV
    result.raw_url = "https://www.pixiv.net/artworks/1"

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False),
        media_placeholders=["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"],
    )
    assert markdown.index("图集") < markdown.index("tg://photo?id=m0") < markdown.index("tg://photo?id=m1")
    assert markdown.index("tg://photo?id=m1") < markdown.index("<footer>")


def test_rich_markdown_omits_missing_metadata():
    from parsehub.types import MultimediaParseResult, Platform

    result = MultimediaParseResult(content="无统计数据")
    result.platform = Platform.THREADS
    result.raw_url = "https://www.threads.com/@u/post/x"

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False),
        lang="zh-hans", view_label="查看",
    )
    assert "<footer>" in markdown
    assert "查看" not in markdown
    assert " ·  · " not in markdown


def test_rich_markdown_respects_hide_source():
    from parsehub.types import MultimediaParseResult, Platform

    result = MultimediaParseResult(content="正文")
    result.platform = Platform.TWITTER
    result.raw_url = "https://x.com/u/status/1"

    markdown = build_rich_markdown(
        result, config=types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=True)
    )
    assert "Source" not in markdown


def test_caption_without_metadata_is_unchanged():
    caption = build_caption_by_str("Title", "Body", "https://x.com/u/status/1", author_name="Author")
    assert "·" not in caption
