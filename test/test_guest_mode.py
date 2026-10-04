"""guest 查询的入口判定: 门禁 + 纯链接 + 有 query id 才回。

guest 是"任何群都能召唤"的入口, 三条前置条件缺一不可。
实际的解析/发送在 `_answer` 里 (需要真实网络), 这里把它换掉, 只验证入口逻辑。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugins.parse import guest as guest_mod


def _msg(text=None, caption=None, user_id=1879026273, guest_query_id="q1"):
    return SimpleNamespace(
        text=text,
        caption=caption,
        guest_query_id=guest_query_id,
        from_user=SimpleNamespace(id=user_id) if user_id else None,
        chat=SimpleNamespace(id=-1001234567890),
    )


def _run(msg, allowed=True, locale="zh-hans") -> AsyncMock:
    """跑一次 guest_parse; 返回被 mock 的 _answer 以便断言。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_session():
        yield None

    answer = AsyncMock(return_value=True)
    with (
        patch.object(guest_mod.access_gate, "is_allowed", AsyncMock(return_value=allowed)),
        patch.object(guest_mod, "_answer", answer),
        patch.object(guest_mod, "get_session", fake_session),
        patch.object(guest_mod, "UserService", return_value=SimpleNamespace(get_lang=AsyncMock(return_value=locale))),
    ):
        asyncio.run(guest_mod.guest_parse(SimpleNamespace(), msg))
    return answer


LINK = "https://x.com/a/status/123"


def test_bare_link_is_answered():
    answer = _run(_msg(text=LINK))
    answer.assert_awaited_once()
    assert answer.await_args.args[1] == "q1"
    assert answer.await_args.args[2] == LINK


def test_bare_link_in_caption_is_answered():
    """带说明的媒体消息 text 是 None"""
    answer = _run(_msg(caption=LINK))
    answer.assert_awaited_once()


def test_chatter_around_the_link_is_ignored():
    """群里随口提到链接不该触发解析"""
    for text in ("看看这个 " + LINK, LINK + " 很好笑", "普通聊天内容"):
        assert _run(_msg(text=text)).await_count == 0, text


def test_denied_user_is_ignored():
    """门禁不通过时连解析都不做"""
    assert _run(_msg(text=LINK), allowed=False).await_count == 0


def test_missing_guest_query_id_is_ignored():
    """没有 query id 就无从回复, 不该白跑一遍解析"""
    assert _run(_msg(text=LINK, guest_query_id=None)).await_count == 0


def test_missing_user_is_ignored():
    """拿不到发起者就无法做门禁判定 → 拒绝 (fail-closed)"""
    assert _run(_msg(text=LINK, user_id=None), allowed=False).await_count == 0


def test_empty_message_is_ignored():
    assert _run(_msg()).await_count == 0
