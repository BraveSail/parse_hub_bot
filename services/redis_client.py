"""缓存用的 Redis 连接。

为什么要它: 原来的持久缓存在 SQLite 表里, 内存缓存在进程里 —— 换 Redis 之后
**重启不丢**、两层共用一套淘汰语义。

两条纪律:

1. **不能 FLUSHDB / FLUSHALL**。161 上的 redis 是宝塔装的**共用实例**, 别的项目
   可能也在用同一个库。所有 key 带 ``bs.cache_key_prefix`` 前缀, 清空只删前缀内的
   key (``SCAN MATCH`` + ``DEL``)。见 ``services/cache.py``。
2. **Redis 不可用时 bot 必须照常工作**。缓存是加速手段, 不是必需品 ——
   连接/读写失败一律退化成"没有缓存"(继续走完整解析流程), 并记 warning。
   这与"单个平台的配置坏掉不许让整个 bot 下线"是同一条原则。
"""

from __future__ import annotations

import redis.asyncio as aioredis

from core import bs
from log import logger

logger = logger.bind(name="Redis")

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """进程内单例客户端 (连接池由 redis-py 管)。"""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            bs.redis_url,
            encoding="utf-8",
            decode_responses=True,  # 缓存里存的是 JSON 字符串, 直接拿 str 省一层解码
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )
        logger.info(f"Redis 客户端已创建: url={bs.redis_url}, prefix={bs.cache_key_prefix}")
    return _client


def cache_key(*parts: str) -> str:
    """拼缓存 key。**所有 key 都必须经这里** —— 前缀是共用实例上的隔离界线。"""
    return bs.cache_key_prefix + ":".join(parts)


async def get_optional(key: str) -> str | None:
    """读一个 key; Redis 出问题时返回 None (当成没缓存), 不抛给调用方。"""
    try:
        return await get_redis().get(key)
    except Exception as e:  # noqa: BLE001 - 缓存不可用不该影响解析
        logger.warning(f"读取缓存失败 (按未命中处理): key={key} err={type(e).__name__}: {e}")
        return None


async def set_with_ttl(key: str, value: str, ttl_seconds: int) -> bool:
    """写一个带 TTL 的 key; 失败返回 False (调用方不必因此中断)。"""
    try:
        await get_redis().set(key, value, ex=ttl_seconds)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"写入缓存失败: key={key} err={type(e).__name__}: {e}")
        return False


async def delete_keys(keys: list[str]) -> int:
    """删掉若干 key, 返回删掉的条数。"""
    if not keys:
        return 0
    try:
        return int(await get_redis().delete(*keys))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"删除缓存失败: {len(keys)} 个 key err={type(e).__name__}: {e}")
        return 0


async def delete_by_prefix(pattern: str) -> int:
    """删掉**匹配前缀模式**的 key, 返回条数。

    ``SCAN`` 而不是 ``KEYS`` (不阻塞 redis); 分批删。**绝不 FLUSHDB** ——
    这个实例是共用的。
    """
    removed = 0
    try:
        client = get_redis()
        batch: list[str] = []
        async for key in client.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                removed += int(await client.delete(*batch))
                batch.clear()
        if batch:
            removed += int(await client.delete(*batch))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"按前缀清缓存失败: pattern={pattern} err={type(e).__name__}: {e}")
    return removed


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"关闭 Redis 连接失败: {type(e).__name__}: {e}")
        finally:
            _client = None


async def ping() -> bool:
    """连通性检查 (启动日志 / 真机验证用)。"""
    try:
        return bool(await get_redis().ping())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Redis 连接不可用: {type(e).__name__}: {e}")
        return False


__all__: list[str] = [
    "cache_key",
    "close_redis",
    "delete_by_prefix",
    "delete_keys",
    "get_optional",
    "get_redis",
    "ping",
    "set_with_ttl",
]
