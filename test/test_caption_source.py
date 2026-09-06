import pytest
from parsehub import Platform
from parsehub.types import ImageParseResult

from plugins.helpers import build_caption, build_caption_by_str
from repo.settings import SettingsConfig


@pytest.mark.parametrize("platform", list(Platform))
def test_caption_uses_platform_display_name(platform: Platform) -> None:
    result = ImageParseResult(content="Body")
    result.raw_url = "https://example.com/post"
    result.platform = platform

    caption = build_caption(result, config=SettingsConfig())

    assert f"Source\uff08{platform.display_name}\uff09</a>" in caption
    assert f"href='{result.raw_url}'" in caption


@pytest.mark.parametrize(
    ("raw_url", "platform"),
    [
        ("https://x.com/example/status/1234567890", Platform.TWITTER),
        ("https://twitter.com/example/status/1234567890", Platform.TWITTER),
        ("https://www.xiaohongshu.com/explore/abc123", Platform.XHS),
        ("https://www.douyin.com/video/1234567890", Platform.DOUYIN),
    ],
)
def test_cached_caption_detects_platform_from_url(raw_url: str, platform: Platform) -> None:
    caption = build_caption_by_str("Title", "Body", raw_url)

    assert f"Source\uff08{platform.display_name}\uff09</a>" in caption


@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize("telegraph_url", [None, "https://telegra.ph/example"])
def test_source_platform_is_preserved_in_caption_modes(rich: bool, telegraph_url: str | None) -> None:
    caption = build_caption_by_str(
        "Title",
        "Body",
        "https://x.com/example/status/1234567890",
        telegraph_url,
        rich=rich,
    )

    assert "Source\uff08Twitter\uff09</a>" in caption


def test_unknown_platform_keeps_plain_source() -> None:
    caption = build_caption_by_str("", "Body", "https://example.com/post")

    assert "Source</a>" in caption
    assert "\uff08" not in caption


def test_hidden_source_hides_platform_too() -> None:
    caption = build_caption_by_str("", "Body", "https://x.com/example/status/1234567890", hide_source=True)

    assert caption == "Body"
