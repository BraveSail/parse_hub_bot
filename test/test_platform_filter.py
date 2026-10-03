"""平台过滤器的健壮性。

聊天消息的 ``text`` / ``caption`` 都可能是 None (纯媒体消息、纯图片带说明)。
过滤器不能因此抛异常 —— 抛出去的表现是"这条消息莫名不解析", 很难查。
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyrogram.types import MessageOriginChannel

from plugins import filters as flt


def _channel_forward(text=None, caption=None):
    """伪造一条"频道自动转发到群"的消息。"""
    return SimpleNamespace(
        text=text,
        caption=caption,
        forward_origin=MessageOriginChannel(chat=SimpleNamespace(id=-1001234567890), message_id=1),
        automatic_forward=True,
        chat=SimpleNamespace(id=-1001234567890, type="supergroup"),
        from_user=SimpleNamespace(id=1),
    )


def _run_filter(update, disabled_platforms=()) -> bool:
    """跑一次过滤器; disabled_platforms 是这个频道配置里禁用的平台。

    返回值语义: True = 允许群里的解析继续 (频道自己没解析这条)。
    """
    cli = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace()))
    settings = SimpleNamespace(
        get_config=AsyncMock(return_value=SimpleNamespace(disabled_platforms=list(disabled_platforms)))
    )

    @asynccontextmanager
    async def fake_session():
        yield None

    with (
        patch("plugins.filters.get_session", fake_session),
        patch("plugins.filters.SettingsService", return_value=settings),
    ):
        return asyncio.run(flt._allow_channel_auto_forward_parse_filter(None, cli, update))


LINK = "https://x.com/a/status/1"


def test_text_link_is_read():
    """频道禁了 twitter → 频道不会解析 → 群里允许解析"""
    assert _run_filter(_channel_forward(text=LINK), disabled_platforms=["twitter"]) is True


def test_caption_link_is_read():
    """带说明的媒体消息 text 是 None —— 必须看 caption, 否则这条永远漏解析"""
    assert _run_filter(_channel_forward(caption=f"图里这个 {LINK}"), disabled_platforms=["twitter"]) is True


def test_caption_is_used_when_text_is_absent():
    """同样的内容放在 caption 里要得到与 text 相同的判定"""
    via_text = _run_filter(_channel_forward(text=LINK), disabled_platforms=["twitter"])
    via_caption = _run_filter(_channel_forward(caption=LINK), disabled_platforms=["twitter"])
    assert via_text == via_caption is True


def test_channel_parses_it_so_group_skips():
    """频道没禁用该平台 (频道自己会解析) → 群里不重复解析"""
    assert _run_filter(_channel_forward(caption=LINK)) is False


def test_no_link_is_not_parsed():
    assert _run_filter(_channel_forward(caption="今天就拍了张照片")) is False


def test_no_text_and_no_caption_does_not_raise():
    """纯媒体消息 (text 与 caption 都是 None) 不能抛异常"""
    assert _run_filter(_channel_forward()) is False


def test_platform_lookup_is_safe_for_empty_input():
    """底层平台识别对 None/空串要返回 None, 而不是 AttributeError"""
    from services import ParseService

    parser = ParseService().parser
    assert parser.get_platform(None) is None
    assert parser.get_platform("") is None
    assert parser.get_platform("普通聊天内容") is None
    assert parser.get_platform(LINK) is not None
