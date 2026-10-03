import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pyrogram.parser import Parser

from plugins.helpers import build_caption_by_str, build_metadata_line
from services.cache import CacheEntry, CacheParseResult, PersistentCache


@pytest.mark.parametrize(
    ("author", "versioned", "hit"),
    [("", False, False), ("Author", False, True), ("", True, True), ("Author", True, True)],
)
def test_cache_refreshes_only_legacy_entries_missing_author(author, versioned, hit):
    payload = {"parse_result": {"title": "T", "author_name": author, "published_at": None, "view_count": None}}
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


def test_caption_without_metadata_is_unchanged():
    caption = build_caption_by_str("Title", "Body", "https://x.com/u/status/1", author_name="Author")
    assert "·" not in caption
