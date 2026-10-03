"""inline 富文本的媒体剥离、结果项构成与回答兜底 (离线, 不联网)。"""

import asyncio
from datetime import UTC, datetime

from parsehub.types import ImageRef, MultimediaParseResult, Platform, RichTextParseResult, VideoRef
from pyrogram.types import (
    InlineQueryResultArticle,
    InputRichMessage,
    InputRichMessageContent,
    InputTextMessageContent,
)

from plugins.parse.inline import (
    _is_rich_result,
    build_inline_results,
    inline_cover_url,
    inline_reply_markup,
    strip_media_markdown,
)


def test_strip_media_markdown_removes_images():
    """inline 回答查询时不支持外部媒体, 正文里的图片语法必须剥掉"""
    assert strip_media_markdown("前\n\n![](https://a.com/x.jpg)\n\n后") == "前\n\n\n\n后"


def test_strip_media_markdown_keeps_text_and_links():
    md = "看 [文字](https://a.com) 与 **粗体**"
    assert strip_media_markdown(md) == md


def test_strip_media_markdown_handles_alt_text():
    assert strip_media_markdown("![封面](https://a.com/p.png) 正文").strip() == "正文"


def test_strip_media_markdown_handles_empty():
    assert strip_media_markdown("") == ""
    assert strip_media_markdown(None) == ""


def test_is_rich_result_detects_rich_article():
    rich = InlineQueryResultArticle(
        title="t", input_message_content=InputRichMessageContent(InputRichMessage(markdown="x"))
    )
    plain = InlineQueryResultArticle(title="t", input_message_content=InputTextMessageContent("x"))
    assert _is_rich_result(rich) is True
    assert _is_rich_result(plain) is False


# ── 结果项构成 ────────────────────────────────────────────────


def make_result(media=None, **kwargs):
    result = MultimediaParseResult(content="正文**粗体**", media=media, **kwargs)
    result.platform = Platform.TWITTER
    result.raw_url = "https://x.com/u/status/1"
    result.published_at = datetime(2026, 10, 3, 11, 0, tzinfo=UTC)
    result.view_count = 1455
    return result


def test_reply_markup_only_for_media():
    """键盘是用来换 inline_message_id 的, 只有需要二次编辑 (补媒体) 时才挂"""
    assert inline_reply_markup(make_result()) is None
    assert inline_reply_markup(make_result(media=[ImageRef(url="https://a/x.jpg")])) is not None


def test_rich_result_has_id_and_no_external_media_syntax():
    result = make_result(media=[VideoRef(url="https://a/v.mp4", width=8, height=6)])
    results = asyncio.run(build_inline_results(result, None, "zh-hans", _config()))
    assert len(results) == 1
    item = results[0]
    assert item.id == "rich"  # 选中回调靠它识别
    markdown = item.input_message_content.rich_message.markdown
    assert "![" not in markdown
    assert "<footer>" in markdown


def test_rich_result_strips_images_from_note_content():
    """长文正文自带 ![](url) 时也要剥掉, 否则整条 inline 回答被 Telegram 拒"""
    result = RichTextParseResult(title="T", markdown_content="正文\n\n![](https://a/inline.png)")
    result.platform = Platform.TWITTER
    result.raw_url = "https://x.com/u/status/1"
    results = asyncio.run(build_inline_results(result, None, "zh-hans", _config()))
    markdown = results[0].input_message_content.rich_message.markdown
    assert "![" not in markdown
    assert "正文" in markdown


def test_inline_cover_url_prefers_video_thumb():
    """视频/动图用平台缩略图当封面, 图片用自己的地址"""
    from parsehub.types import AniRef

    assert inline_cover_url(make_result(media=[VideoRef(url="https://a/v.mp4", thumb_url="https://a/c.jpg")])) == "https://a/c.jpg"
    assert inline_cover_url(make_result(media=[AniRef(url="https://a/a.mp4", thumb_url="https://a/ac.jpg")])) == "https://a/ac.jpg"
    assert inline_cover_url(make_result(media=[ImageRef(url="https://a/i.jpg", thumb_url="https://a/t.jpg")])) == "https://a/t.jpg"


def test_inline_cover_url_without_media():
    assert inline_cover_url(make_result()) == ""


def test_media_result_uses_cover_placeholder():
    """有封面时消息里先出现一张图 (老体验), 而不是纯文字"""
    results = asyncio.run(build_inline_results(make_result(media=[VideoRef(url="https://a/v.mp4", thumb_url="https://a/c.jpg")]), None, "zh-hans", _config()))
    item = results[0]
    assert type(item).__name__ == "InlineQueryResultPhoto"
    assert item.photo_url == "https://a/c.jpg"
    assert item.id == "rich"
    assert item.reply_markup is not None  # 需要句柄才能在选中后替换


def test_single_media_ref_does_not_break_dimensions():
    """media 是单个 ref (非 list) 时取尺寸不能抛"""
    results = asyncio.run(build_inline_results(make_result(media=VideoRef(url="https://a/v.mp4", thumb_url="https://a/c.jpg", width=1080, height=1920)), None, "zh-hans", _config()))
    assert results[0].photo_width == 1080


def _config():
    from repo.settings import SettingsConfig

    return SettingsConfig()
