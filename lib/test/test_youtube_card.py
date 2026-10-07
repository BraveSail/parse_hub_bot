"""正文里的 YouTube 链接 -> 引用卡片 + 封面。

需求（用户）：推文正文里引用的 YouTube 链接要**像 bilibili 引用的视频那样** ——
有封面、标题能点开。抓不到封面时什么都不加：封面是锦上添花，不该让解析失败。
"""

import asyncio
from unittest.mock import patch

import pytest

from parsehub.provider_api.youtube import YoutubeCard, find_youtube_links
from parsehub.types import ImageRef

LINK = "http://www.youtube.com/@SuikodenTheAnime-EN"


# ── 链接识别 ───────────────────────────────────────────────────────────


def test_finds_channel_and_video_links():
    text = f"看这个 {LINK} 还有 https://youtu.be/dQw4w9WgXcQ"
    assert find_youtube_links(text) == [LINK, "https://youtu.be/dQw4w9WgXcQ"]


def test_no_links_returns_empty():
    assert find_youtube_links("纯文字没有链接") == []
    assert find_youtube_links(None) == []


def test_links_are_deduplicated_keeping_order():
    assert find_youtube_links(f"{LINK}\n{LINK}") == [LINK]


def test_trailing_punctuation_is_trimmed():
    """中文句读跟在链接后面, 不能算进链接"""
    assert find_youtube_links(f"{LINK}。") == [LINK]


# ── 抓卡片 (打桩网络) ──────────────────────────────────────────────────


def _card(title="频道名", cover="https://yt3.example/x", url=LINK) -> YoutubeCard:
    return YoutubeCard(url=url, title=title, cover_url=cover)


@pytest.mark.parametrize(
    ("text", "card", "want_quote", "want_media"),
    [
        (f"正文 {LINK}", _card(), True, 1),
        # 抓不到 -> 什么都不加, 正文照旧
        (f"正文 {LINK}", None, False, 0),
        ("正文没有链接", _card(), False, 0),
    ],
)
def test_twitter_adds_card_only_when_the_cover_is_available(text, card, want_quote, want_media):
    from parsehub.parsers.parser.twitter import TwitterParser

    async def fake_card(_url):
        return card

    with patch("parsehub.provider_api.youtube.fetch_card", new=fake_card):
        quote, media = asyncio.run(TwitterParser._youtube_card(text))

    assert bool(quote) is want_quote
    assert len(media) == want_media
    if want_quote:
        assert quote.startswith("> <a href=")
        assert LINK in quote
        assert "频道名" in quote
        assert isinstance(media[0], ImageRef)
        assert media[0].url == "https://yt3.example/x"


def test_title_and_url_are_escaped():
    """标题里的 & 与引号必须转义, 否则 HTML 解析会出错"""
    from parsehub.parsers.parser.twitter import TwitterParser

    async def fake_card(_url):
        return _card(title='Rock & Roll "Live"')

    with patch("parsehub.provider_api.youtube.fetch_card", new=fake_card):
        quote, _ = asyncio.run(TwitterParser._youtube_card(f"看 {LINK}"))

    assert "&amp;" in quote
    assert "&quot;" in quote or '"' not in quote.split(">")[-2]


def test_only_the_first_resolvable_link_is_used():
    """一条推文塞多张封面会喧宾夺主, 只取第一个抓得到的"""
    from parsehub.parsers.parser.twitter import TwitterParser

    calls: list[str] = []

    async def fake_card(url):
        calls.append(url)
        # 第一个抓不到、第二个抓得到
        return _card() if url.endswith("bbB") else None

    with patch("parsehub.provider_api.youtube.fetch_card", new=fake_card):
        quote, media = asyncio.run(
            TwitterParser._youtube_card("https://youtu.be/aaaaaaaaaaA https://youtu.be/bbbbbbbbbbB")
        )

    assert calls == ["https://youtu.be/aaaaaaaaaaA", "https://youtu.be/bbbbbbbbbbB"]
    assert bool(quote) and len(media) == 1
