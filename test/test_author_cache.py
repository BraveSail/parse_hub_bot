import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pyrogram.parser import Parser

from plugins.helpers import build_caption_by_str
from services.cache import CacheEntry, CacheParseResult, PersistentCache


@pytest.mark.parametrize(
    ("author", "versioned", "hit"),
    [("", False, False), ("Author", False, True), ("", True, True), ("Author", True, True)],
)
def test_cache_refreshes_only_legacy_entries_missing_author(author, versioned, hit):
    payload = {"parse_result": {"title": "T", "author_name": author}}
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
