"""Facebook 自研解析（不依赖 yt-dlp）的行为验证。

fixture 从**真实页面**裁剪而来（只留解析需要的 ``data-sjs`` JSON 块 + og 元信息，
字段值未改动），覆盖实测遇到的三种页面形态：

- ``facebook_watch.html`` —— ``/watch/?v=<id>``（``data.video.story...media``）
- ``facebook_post.html``  —— ``/<user>/videos/<id>`` 帖子页（作者挂在外层 story 的 ``actors``）
- ``facebook_reel.html``  —— ``/reel/<id>``（``short_form_video_context.playback_video``）

直链形态结论：页面内嵌 JSON 里有**明文** ``browser_native_hd_url`` / ``browser_native_sd_url``
（``*.fbcdn.net`` 的 mp4），匿名可下（实测 HTTP 206 ``video/mp4``）。只有 DASH manifest、
拿不到渐进式地址时明确抛错（不半吊子下载 MPD）。
"""

import asyncio
from pathlib import Path

import pytest

from parsehub.parsers.parser.facebook import FacebookParse
from parsehub.provider_api.facebook import (
    FacebookAPIError,
    _iter_dicts,
    _json_blocks,
    _lookup,
    parse_video_html,
)

FIXTURES = Path(__file__).parent / "fixtures"

WATCH_ID = "647537299265662"
POST_ID = "6968553779868435"
REEL_ID = "1195289147628387"


def _html(name: str) -> str:
    return FIXTURES.joinpath(name).read_text(encoding="utf-8")


# ── ① 解析：直链 / 标题 / 时长 / 宽高 ──────────────────────────────────────


def test_watch_page_parses_direct_url_and_metadata():
    video = parse_video_html(_html("facebook_watch.html"), WATCH_ID)

    assert video.video_id == WATCH_ID
    # 直链是真正的媒体地址（fbcdn 的 mp4），不是页面地址
    assert video.url.startswith("https://") and ".fbcdn.net/" in video.url
    assert ".mp4" in video.url
    assert "facebook.com/watch" not in video.url

    assert video.title.startswith("Padre enseña a su hijo")
    assert video.content.startswith("Padre enseña a su hijo")
    assert video.author_name == "InfoPico"
    assert video.author_url == "https://www.facebook.com/infopico"

    assert video.duration == pytest.approx(136.179, abs=0.01)
    assert (video.width, video.height) == (720, 1280)
    assert video.published_at == 1605534618
    assert video.view_count == 19573119
    assert video.thumb_url.startswith("https://") and video.thumb_url


def test_hd_url_is_preferred_over_sd():
    """有 HD 就用 HD（实测同页同时给 sd/hd 两条渐进式直链）。"""
    html = _html("facebook_watch.html")
    video = parse_video_html(html, WATCH_ID)
    hd = _first_url(html, "browser_native_hd_url")
    sd = _first_url(html, "browser_native_sd_url")
    assert hd and sd and hd != sd
    assert video.url == hd


def test_post_page_parses_author_from_outer_story():
    """帖子页的 media 节点自身没有作者，作者挂在外层 story 的 actors 上。"""
    video = parse_video_html(_html("facebook_post.html"), POST_ID)

    assert video.url.startswith("https://") and ".fbcdn.net/" in video.url
    assert video.author_name == "ATTN:"
    assert video.author_url == "https://www.facebook.com/attn"
    assert video.width == 1080 and video.height == 1080
    assert video.duration == pytest.approx(132.675, abs=0.01)


def test_reel_page_parses_short_form_context():
    """reel 页直链在 short_form_video_context.playback_video，作者在 video_owner。"""
    video = parse_video_html(_html("facebook_reel.html"), REEL_ID)

    assert video.video_id == REEL_ID
    assert video.url.startswith("https://") and ".fbcdn.net/" in video.url
    assert video.author_name == "Beast Camp Training"
    assert video.author_url == "https://www.facebook.com/beastcamptraining"
    assert video.duration == pytest.approx(9.579, abs=0.01)
    assert (video.width, video.height) == (480, 848)


def _first_url(html: str, key: str) -> str:
    """从页面内嵌 JSON 里取出某个直链字段的原始值（用模块自己的遍历逻辑）。"""
    for block in _json_blocks(html):
        for node in _iter_dicts(block):
            value = _lookup(node, key)
            if isinstance(value, str) and value.startswith("http"):
                return value
    raise AssertionError(f"fixture 里应内嵌 {key}")


# ── ③ 拿不到数据时抛错 ─────────────────────────────────────────────────────


def test_missing_video_raises():
    login_wall = '<html><body><form id="login_form"></form><div>You must log in to continue</div></body></html>'
    with pytest.raises(FacebookAPIError):
        parse_video_html(login_wall, "123")


def test_url_id_not_in_page_raises_instead_of_wrong_video():
    """URL 里的 id 在页面里找不到时必须报错 —— 绝不能静默下成"相关视频"那条。"""
    with pytest.raises(FacebookAPIError):
        parse_video_html(_html("facebook_watch.html"), "999999999")


def test_dash_only_page_raises():
    """只有 DASH manifest、没有渐进式直链时明确抛错（不半吊子解析 MPD）。"""
    dash_only = (
        '<html><body><script type="application/json" data-sjs>'
        '{"require":[{"__bbox":{"result":{"data":{"video":{"story":{"attachments":[{"media":'
        '{"__typename":"Video","id":"123","videoDeliveryLegacyFields":'
        '{"id":"123","dash_manifest_xml_string":"<?xml version=\\"1.0\\"?><MPD></MPD>"}}}]}}}}}]}}'
        "</script></body></html>"
    )
    with pytest.raises(FacebookAPIError, match="DASH"):
        parse_video_html(dash_only, "123")


# ── ② 匹配规则与 v 参数保留 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/watch/?v=647537299265662",
        "https://www.facebook.com/reel/1195289147628387",
        "https://www.facebook.com/infopico/videos/647537299265662/",
        "https://www.facebook.com/share/v/AbCdEf123/",
        "https://www.facebook.com/share/r/AbCdEf123/",
    ],
)
def test_match(url):
    assert FacebookParse.match(url) is True


def test_non_video_url_does_not_match():
    assert FacebookParse.match("https://www.facebook.com/infopico") is False


def test_v_parameter_is_reserved_and_others_stripped():
    """``v`` 必须保留（定位视频），其余跟踪参数清掉。"""
    parser = FacebookParse()
    raw = asyncio.run(parser.get_raw_url("https://www.facebook.com/watch/?v=647537299265662&ref=share&__tn__=abc"))
    assert "v=647537299265662" in raw
    assert "ref=" not in raw
    assert "__tn__" not in raw


# ── ④ parser 产出的 VideoRef.url 是真正的媒体地址 ──────────────────────────


def test_parser_result_video_url_is_media_not_page(monkeypatch):
    """``_do_parse`` 产出的 ``VideoRef.url`` 必须是 fbcdn 直链，不是页面 URL。"""
    from parsehub.provider_api.facebook import FacebookAPI

    html = _html("facebook_watch.html")

    async def fake_get_video(self, url):  # noqa: ANN001, ARG001
        return parse_video_html(html, WATCH_ID)

    monkeypatch.setattr(FacebookAPI, "get_video", fake_get_video)

    result = asyncio.run(FacebookParse()._do_parse(f"https://www.facebook.com/watch/?v={WATCH_ID}"))

    video = result.media
    assert video is not None
    assert video.url.startswith("https://") and ".fbcdn.net/" in video.url
    assert ".mp4" in video.url
    assert "facebook.com" not in video.url
    assert video.duration == 136
    assert (video.width, video.height) == (720, 1280)
    assert result.title.startswith("Padre enseña")
