"""linux.do 纯图话题: 走 ImageParseResult 的那条分支不能崩。

`ImageParseResult` 的图片参数叫 ``photo`` 而 `RichTextParseResult` 叫 ``media``
—— 之前把 ``media`` 放进公共字典传给两个类, 纯图话题 (markdown_content 为空)
必抛 TypeError: unexpected keyword argument 'media'。pylint E1123 抓到, 这里锁住。
"""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.parsers.parser.linuxdo import (
    LinuxDoImageParseResult,
    LinuxDoParser,
    LinuxDoRichTextParseResult,
)
from parsehub.provider_api.linuxdo import LinuxDoImage, LinuxDoTopic


def _topic(**over):
    topic = LinuxDoTopic(topic_id="1", title="标题")
    for k, v in over.items():
        setattr(topic, k, v)
    return topic


def _parse(topic):
    with patch.object(LinuxDoTopic, "parse", new=AsyncMock(return_value=topic)):
        return asyncio.run(LinuxDoParser()._do_parse("https://linux.do/t/1"))


def test_image_only_topic_uses_the_photo_parameter():
    """纯图话题: markdown_content 为空 -> 走 ImageParseResult(photo=...), 不能崩"""
    result = _parse(_topic(images=[LinuxDoImage(url="https://cdn.ldstatic.com/a.png", width=400, height=300)]))
    assert isinstance(result, LinuxDoImageParseResult)
    assert [m.url for m in (result.media or [])] == ["https://cdn.ldstatic.com/a.png"]


def test_richtext_topic_carries_media_and_the_context_tail():
    """图文话题: 走 RichTextParseResult(media=...), 并带上引用块的媒体计数

    （上下文块排在正文前 ⇒ 走 ``reply_media_count`` 那一档）
    """
    result = _parse(
        _topic(
            markdown_content="正文",
            images=[LinuxDoImage(url="https://cdn.ldstatic.com/q.png")],
            reply_media_count=1,
        )
    )
    assert isinstance(result, LinuxDoRichTextParseResult)
    assert result.markdown_content == "正文"
    assert [m.url for m in (result.media or [])] == ["https://cdn.ldstatic.com/q.png"]
    assert result.reply_media_count == 1


def test_image_topic_without_images():
    """纯文字但没 markdown 的话题也不能崩 (media 为空)"""
    result = _parse(_topic(text_content="只有文字"))
    assert isinstance(result, LinuxDoImageParseResult)
    assert result.media is None
