"""正文里的 YouTube 链接 -> 引用卡片 + 封面。

需求（用户）：推文正文里引用的 YouTube 链接要**像 bilibili 引用的视频那样** ——
有封面、标题能点开。抓不到封面时什么都不加：封面是锦上添花，不该让解析失败。
"""

import asyncio
from unittest.mock import patch

import pytest

from parsehub.provider_api.youtube import YoutubeCard, find_youtube_links

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
    ("text", "card", "want_quote"),
    [
        (f"正文 {LINK}", _card(), True),
        # 抓不到 -> 什么都不加, 正文照旧
        (f"正文 {LINK}", None, False),
        ("正文没有链接", _card(), False),
    ],
)
def test_twitter_adds_a_text_card_and_never_a_cover_media(text, card, want_quote):
    """**核心契约（2026-10-08 用户纠正）**：链接卡片只出**文字行**（标题可点），
    **不能**把 YouTube 封面当独立媒体发出去。

    用户原话：「我之前让弄的是**给视频加封面**，不是把封面再发一遍」。
    给视频配封面走的是另一个机制（``VideoRef.thumb_url`` → ``prepare_video_thumbs``
    → ``InputMediaVideo(thumb=…)``），与链接卡片无关。
    """
    from parsehub.parsers.parser.twitter import TwitterParser

    async def fake_card(_url):
        return card

    with patch("parsehub.provider_api.youtube.fetch_card", new=fake_card):
        quote, media = asyncio.run(TwitterParser._youtube_card(text))

    assert bool(quote) is want_quote
    assert media == [], "链接卡片不得产出任何媒体（封面不是独立图片）"
    if want_quote:
        assert quote.startswith("> <i><a href=")
        assert LINK in quote
        assert "频道名" in quote


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
    assert bool(quote) and media == []
