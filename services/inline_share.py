"""inline 多图场景的 start 参数映射。

Telegram 的 `answerInlineQuery` 支持 `switch_pm` 参数: 在 inline 结果上方显示一个
按钮, 点击后切到 bot 私聊并发送 `/start <参数>`。参数只能是 `A-Za-z0-9_-` 且
不超过 64 字符, 装不下链接本身, 所以用随机 token 映射回原始 URL。

映射只活在进程内存里 (用户在 inline 列表点按钮通常就在几秒内), bot 重启后
token 失效, `/start` 会回落到"链接已失效"提示。
"""

from __future__ import annotations

import secrets

from services.cache import TTLCache

INLINE_START_TOKEN_BYTES = 16
"""token 的随机字节数 (token_urlsafe → 22 字符, 满足 1-64 字符与字符集限制)"""

INLINE_START_TTL = 60 * 60
"""映射有效期 (秒)。用户可能过一会儿才点按钮, 给足一小时。"""

INLINE_START_MAX_ENTRIES = 10000
"""映射条数上限, 超出按写入顺序淘汰最旧的一条"""


class InlineStartLinkService:
    """`switch_pm_parameter` ↔ 原始链接的短期映射。"""

    def __init__(
        self,
        ttl: float = INLINE_START_TTL,
        maxsize: int = INLINE_START_MAX_ENTRIES,
    ) -> None:
        self._cache: TTLCache = TTLCache(ttl=ttl, cleanup_interval=60, maxsize=maxsize)

    async def register(self, raw_url: str) -> str:
        """登记一条链接, 返回可放进 start 参数的 token。"""
        token = secrets.token_urlsafe(INLINE_START_TOKEN_BYTES)
        await self._cache.set(token, raw_url)
        return token

    async def resolve(self, token: str) -> str | None:
        """token → 原始链接; 未登记或已过期返回 None。"""
        return await self._cache.get(token)


inline_start_link = InlineStartLinkService()
