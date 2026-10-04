"""inline / guest 门禁: 只有与 bot 同在白名单群的用户才放行。

只查一个群 —— 不遍历 bot 所在的全部群, 所以群再多也只是一次 API 调用。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant

from plugins.parse.access import ALLOWED_TTL, DENIED_TTL, AccessGate

GROUP = -1001481033767
USER = 1879026273


def _gate() -> AccessGate:
    return AccessGate()


def _cli(status=ChatMemberStatus.MEMBER, error: Exception | None = None) -> SimpleNamespace:
    if error is not None:
        return SimpleNamespace(get_chat_member=AsyncMock(side_effect=error))
    return SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))


def test_disabled_without_a_configured_group():
    """没配置白名单群 = 不启用门禁 (升级前后行为一致)"""
    gate = _gate()
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=0)):
        cli = _cli()
        assert asyncio.run(gate.is_allowed(cli, USER)) is True
        cli.get_chat_member.assert_not_awaited()


def test_member_is_allowed():
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        assert asyncio.run(_gate().is_allowed(_cli(ChatMemberStatus.MEMBER), USER)) is True


def test_admin_and_owner_are_allowed():
    for status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED):
        with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
            assert asyncio.run(_gate().is_allowed(_cli(status), USER)) is True, status


def test_left_member_is_rejected():
    for status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
            assert asyncio.run(_gate().is_allowed(_cli(status), USER)) is False, status


def test_not_participant_is_rejected():
    """不在群里时 getChatMember 会抛 UserNotParticipant"""
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        assert asyncio.run(_gate().is_allowed(_cli(error=UserNotParticipant()), USER)) is False


def test_api_error_rejects():
    """门禁不能因为 API 抖动而放开 (fail-closed)"""
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        gate = _gate()
        assert asyncio.run(gate.is_allowed(_cli(error=RuntimeError("boom")), USER)) is False


def test_missing_user_or_client_is_rejected():
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        gate = _gate()
        assert asyncio.run(gate.is_allowed(_cli(), None)) is False
        assert asyncio.run(gate.is_allowed(None, USER)) is False


def test_allowed_result_is_cached():
    """重复调用只打一次 Telegram —— 这是不 flood 的关键"""
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        gate = _gate()
        cli = _cli(ChatMemberStatus.MEMBER)
        for _ in range(5):
            assert asyncio.run(gate.is_allowed(cli, USER)) is True
        assert cli.get_chat_member.await_count == 1


def test_denied_result_is_cached_but_shorter():
    """拒绝也缓存 (防止刷接口), 但时长要比放行短"""
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        gate = _gate()
        cli = _cli(error=UserNotParticipant())
        for _ in range(3):
            assert asyncio.run(gate.is_allowed(cli, USER)) is False
        assert cli.get_chat_member.await_count == 1
    assert DENIED_TTL < ALLOWED_TTL


def test_clear_drops_the_cache():
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        gate = _gate()
        cli = _cli(ChatMemberStatus.MEMBER)
        asyncio.run(gate.is_allowed(cli, USER))
        gate.clear()
        asyncio.run(gate.is_allowed(cli, USER))
        assert cli.get_chat_member.await_count == 2


def test_only_one_group_is_ever_queried():
    """只查配置的那一个群, 不遍历其它群"""
    with patch("plugins.parse.access.bs", SimpleNamespace(guest_whitelist_group_id=GROUP)):
        cli = _cli(ChatMemberStatus.MEMBER)
        asyncio.run(_gate().is_allowed(cli, USER))
        assert cli.get_chat_member.await_args.args[0] == GROUP
