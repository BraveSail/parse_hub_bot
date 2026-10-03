"""inline 富文本的媒体剥离与回答兜底 (离线, 不联网)。"""

from pyrogram.types import (
    InlineQueryResultArticle,
    InputRichMessage,
    InputRichMessageContent,
    InputTextMessageContent,
)

from plugins.parse.inline import _is_rich_result, strip_media_markdown


def test_strip_media_markdown_removes_images():
    """inline 结果不支持外部媒体, 正文里的图片语法必须剥掉"""
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
