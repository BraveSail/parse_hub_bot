"""缓存发送 (file_id 复用) 的失败回退。

Telegram 官方明确说 file_id 可能随时间失效、不建议长期存储。旧实现在发送失败时
只记日志并 `return False` —— 用户**什么都收不到**。现在失败要清掉那条坏缓存并
返回 False, 让调用方继续走正常解析 (重新下载 + 重建缓存)。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugins.parse import handlers


def _run(cached, *, send_error: Exception | None):
    """跑 _try_send_cached, 返回 (是否发出, 是否清了缓存)。"""
    sender = SimpleNamespace()
    req = SimpleNamespace(t_="t", custom_content="", force_spoiler=False)

    removed: list[str] = []

    async def fake_remove(url: str) -> None:
        removed.append(url)

    send = AsyncMock(side_effect=send_error) if send_error else AsyncMock(return_value=None)
    with (
        patch.object(handlers, "send_cached", new=send),
        patch.object(handlers.persistent_cache, "remove", new=fake_remove),
    ):
        ok = asyncio.run(handlers._try_send_cached(sender, cached, "https://x.com/a/status/1", req))
    return ok, removed


def test_successful_cache_send_reports_sent():
    ok, removed = _run(cached=object(), send_error=None)
    assert ok is True
    assert removed == []          # 成功了不动缓存


def test_failed_cache_send_drops_the_entry_and_reports_not_sent():
    """file_id 失效: 清掉坏缓存并返回 False, 让调用方重新解析"""
    ok, removed = _run(cached=object(), send_error=Exception("Bad Request: wrong file identifier"))
    assert ok is False
    assert removed == ["https://x.com/a/status/1"]


def test_try_send_cached_passes_the_spoiler_flag():
    """缓存命中时也要把 /s 传下去 —— 这正是"发过 /s 不生效"的根因所在:

    缓存路径原先直接发送、不带 force_spoiler, 于是先发普通链接建立缓存后,
    再对同一链接加 /s 会从 file_id 缓存发出**没遮**的版本。
    """
    req = SimpleNamespace(t_="t", custom_content="", force_spoiler=True)
    seen: dict = {}

    async def fake_send(sender, cached, url, **kwargs):
        seen.update(kwargs)

    with patch.object(handlers, "send_cached", new=fake_send):
        ok = asyncio.run(handlers._try_send_cached(SimpleNamespace(), object(), "https://x.com/a/status/1", req))

    assert ok is True
    assert seen["force_spoiler"] is True


def test_cache_removal_failure_does_not_raise():
    """清缓存本身失败也不能把异常抛出去 —— 重新解析才是重点"""
    sender = SimpleNamespace()
    req = SimpleNamespace(t_="t", custom_content="", force_spoiler=False)

    async def boom(url: str) -> None:
        raise RuntimeError("db down")

    with (
        patch.object(handlers, "send_cached", new=AsyncMock(side_effect=Exception("bad file_id"))),
        patch.object(handlers.persistent_cache, "remove", new=boom),
    ):
        ok = asyncio.run(handlers._try_send_cached(sender, object(), "https://x.com/a/status/1", req))
    assert ok is False
