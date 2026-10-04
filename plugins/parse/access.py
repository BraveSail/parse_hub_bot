"""inline / guest 查询的白名单门禁。

规则：只认**一个**白名单群 —— 用户必须与 bot 同在该群才能使用 inline 与 guest 查询。
只看这一个群（不遍历 bot 所在的全部群），所以群再多也只是一次 API 调用，不会 flood。

判定按**发起用户**：inline query 拿不到"发生在哪个群"（只有 chat_type），所以只能用
用户维度判断；guest query 虽然带发生群，但场景上同样收敛到用户维度。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError

from core import bs
from log import logger
from services.cache import TTLCache

if TYPE_CHECKING:
    from pyrogram import Client

logger = logger.bind(name="Access")

#: 通过后的缓存时长 (秒)。同一用户短时间内重复调用不该反复打 Telegram。
ALLOWED_TTL = 600

#: 拒绝后的缓存时长 (秒)。要短 —— 用户可能刚刚加入群, 长缓存会让他白等。
DENIED_TTL = 60

ALLOWED_CACHE_MAX = 10000
DENIED_CACHE_MAX = 10000

#: 视为"在群里"的状态；LEFT / BANNED 已不在群
_MEMBER_STATUSES = frozenset(
    {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED,  # 被限制但仍在群
    }
)


class AccessGate:
    """按用户判定能否使用 inline / guest。"""

    def __init__(self) -> None:
        self._allowed: TTLCache = TTLCache(ttl=ALLOWED_TTL, maxsize=ALLOWED_CACHE_MAX)
        self._denied: TTLCache = TTLCache(ttl=DENIED_TTL, maxsize=DENIED_CACHE_MAX)

    @property
    def group_id(self) -> int:
        return int(getattr(bs, "guest_whitelist_group_id", 0) or 0)

    @property
    def enabled(self) -> bool:
        """没配置白名单群 = 不启用门禁 (保持升级前的行为)。"""
        return self.group_id != 0

    async def is_allowed(self, cli: Client | None, user_id: int | None) -> bool:
        """该用户能否使用 inline / guest。

        任何异常都**拒绝**(fail-closed) —— 门禁不能因为 API 抖动而放开。
        """
        if not self.enabled:
            return True
        if user_id is None or cli is None:
            return False

        key = str(user_id)
        if await self._allowed.get(key) is not None:
            return True
        if await self._denied.get(key) is not None:
            return False

        allowed = await self._check_membership(cli, user_id)
        await (self._allowed if allowed else self._denied).set(key, True)
        logger.debug(f"门禁判定: user_id={user_id} allowed={allowed} group={self.group_id}")
        return allowed

    async def _check_membership(self, cli: Client, user_id: int) -> bool:
        try:
            member = await cli.get_chat_member(self.group_id, user_id)
        except RPCError as e:
            # USER_NOT_PARTICIPANT = 不在群里(正常拒绝); 其它错误也一律拒绝
            logger.debug(f"门禁查询失败 (视为不在群): user_id={user_id} error={type(e).__name__}: {e}")
            return False
        except Exception as e:  # noqa: BLE001 - 兜底: 任何异常都不放行
            logger.warning(f"门禁查询异常 (视为不在群): user_id={user_id} error={type(e).__name__}: {e}")
            return False
        status: Any = getattr(member, "status", None)
        return status in _MEMBER_STATUSES

    def clear(self) -> None:
        """清空缓存 (测试与配置变更后用)。TTLCache 没有 clear, 直接换新实例。"""
        self._allowed = TTLCache(ttl=ALLOWED_TTL, maxsize=ALLOWED_CACHE_MAX)
        self._denied = TTLCache(ttl=DENIED_TTL, maxsize=DENIED_CACHE_MAX)


access_gate = AccessGate()

__all__ = ["ALLOWED_TTL", "DENIED_TTL", "AccessGate", "access_gate"]
