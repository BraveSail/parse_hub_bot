"""threads 被回复帖的媒体：不能丢，且要落进回复卡片里。

用户报「回复的图片丢了」。

取证（真实响应，`https://www.threads.com/@yuxiuaaa_/post/DeI4z3cEnXY`）::

    主帖     = 1 个视频
    被回复帖 = 1 张图（838352144_…jpg）   ← 就是丢掉的那张

根因：``_do_parse`` 只把 ``post.media`` 放进结果，``post.reply_to.media`` 从未取用
（对照 twitter 的 ``media.extend(reply_media)`` + ``reply_media_count``）。

渲染层**早就支持**这件事：``plugins/parse/inline_rich.py`` 按 ``reply_media_count``
把 media 末尾那几条切出来，``plugins/helpers.py`` 再塞进回复卡片（``render_quote_card``）。
所以只需把媒体补进结果并给出计数。
"""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.parsers.parser.threads import ThreadsParser
from parsehub.provider_api.threads import ThreadsMedia, ThreadsMediaType, ThreadsPost
from parsehub.types import ImageRef, VideoRef

URL = "https://www.threads.com/@yuxiuh/post/DeI4z3cEnXY"

MAIN_VIDEO = "https://cdn.example/main.mp4"
REPLY_IMAGE = "https://cdn.example/reply.jpg"


def _media(kind: ThreadsMediaType, url: str) -> ThreadsMedia:
    return ThreadsMedia(type=kind, url=url, thumb_url=url, width=100, height=200)


def _post(*, content: str = "正文", media=None, reply_to: ThreadsPost | None = None) -> ThreadsPost:
    return ThreadsPost(content=content, media=media, reply_to=reply_to, author_name="主", author_handle="main")


def _parse(post: ThreadsPost):
    with patch.object(ThreadsParser, "_parse", AsyncMock(return_value=post)):
        return asyncio.run(ThreadsParser()._do_parse(URL))


def test_the_replied_post_media_is_included():
    """**核心**: 被回复帖的图必须在结果媒体里（用户报的就是它丢了）"""
    reply = ThreadsPost(
        content="引用正文",
        media=_media(ThreadsMediaType.IMAGE, REPLY_IMAGE),
        author_name="Chloe",
        author_handle="bengbeng_ka",
    )
    result = _parse(_post(media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO), reply_to=reply))

    assert len(result.media) == 2, [type(m).__name__ for m in result.media]
    assert isinstance(result.media[0], VideoRef)
    assert isinstance(result.media[1], ImageRef)
    assert result.media[1].url == REPLY_IMAGE


def test_the_reply_count_points_at_the_tail():
    """``reply_media_count`` 是渲染层切分的依据 —— 它必须等于回复媒体的条数。

    错了的后果不是"少一张图"，而是**把正文的图算进回复卡片**（或反之）。
    """
    reply = ThreadsPost(content="引用", media=_media(ThreadsMediaType.IMAGE, REPLY_IMAGE))
    result = _parse(_post(media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO), reply_to=reply))
    assert result.reply_media_count == 1


def test_a_post_without_a_reply_keeps_a_zero_count():
    result = _parse(_post(media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO)))
    assert len(result.media) == 1
    assert result.reply_media_count == 0


def test_a_reply_without_media_does_not_eat_the_main_media():
    """被回复帖没图时计数必须是 0 —— 否则会把主帖的图错当成回复卡片里的"""
    result = _parse(_post(media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO), reply_to=ThreadsPost(content="纯文字回复")))
    assert len(result.media) == 1
    assert isinstance(result.media[0], VideoRef)
    assert result.reply_media_count == 0


def test_a_reply_carousel_contributes_every_item():
    """被回复帖是多图（carousel）时每条都要进来"""
    reply = ThreadsPost(
        content="引用",
        media=[
            _media(ThreadsMediaType.IMAGE, "https://cdn.example/r1.jpg"),
            _media(ThreadsMediaType.IMAGE, "https://cdn.example/r2.jpg"),
        ],
    )
    result = _parse(_post(media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO), reply_to=reply))
    assert len(result.media) == 3, [type(m).__name__ for m in result.media]
    assert result.reply_media_count == 2


def test_no_media_at_all_stays_empty():
    result = _parse(_post(content="纯文字"))
    assert list(result.media) == []
    assert result.reply_media_count == 0


def test_the_quote_block_still_carries_the_reply_text():
    """补媒体不能影响引用块的文字（那段是 ``format_quote_block`` 渲染的）"""
    reply = ThreadsPost(
        content="这也不让问，要闹哪样",
        media=_media(ThreadsMediaType.IMAGE, REPLY_IMAGE),
        author_name="Chloe",
        author_handle="bengbeng_ka",
    )
    result = _parse(_post(content="主帖正文", media=_media(ThreadsMediaType.VIDEO, MAIN_VIDEO), reply_to=reply))
    assert "这也不让问" in result.content, result.content
    assert "主帖正文" in result.content, result.content
    assert "Chloe" in result.content, result.content


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
