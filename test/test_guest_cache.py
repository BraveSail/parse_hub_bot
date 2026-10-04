"""guest 的 file_id 缓存: 命中就直接发, 不再下载/上传。

guest 以前完全没接缓存 —— 同一个链接被反复 @, 每次都重新下载 + 上传。
现在与私聊/群/inline 一致: 首次发送后把 file_id 存进 persistent_cache,
之后再有人请求同一链接直接复用 (跳过解析/下载/转码/上传)。
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.parse import guest as guest_mod
from services.cache import CacheEntry, CacheParseResult


def _cached_entry() -> CacheEntry:
    return CacheEntry(
        parse_result=CacheParseResult(title="标题", content="正文", author_name="作者"),
        media=[],
        rich=True,
    )


def _run_answer(*, cached, pipeline_should_run: bool):
    """跑一次 _answer, 返回 (ParsePipeline mock, _deliver mock)。"""

    @asynccontextmanager
    async def fake_session():
        yield None

    pipeline_cls = MagicMock()

    def _pipeline(*a, **kw):  # 不该被调用 (命中缓存时)
        if not pipeline_should_run:
            raise AssertionError("命中缓存时不该启动 ParsePipeline")
        ctx = MagicMock()
        ctx.run = AsyncMock(return_value=None)
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    pipeline_cls.side_effect = _pipeline
    deliver = AsyncMock(return_value=None)

    with (
        patch.object(guest_mod, "get_session", fake_session),
        patch.object(
            guest_mod,
            "SettingsService",
            return_value=SimpleNamespace(get_config_by_user=AsyncMock(return_value=SimpleNamespace(video_cover=False))),
        ),
        patch.object(
            guest_mod,
            "ParseService",
            return_value=SimpleNamespace(get_raw_url=AsyncMock(return_value="https://x.com/a/status/1")),
        ),
        patch.object(guest_mod.persistent_cache, "get", AsyncMock(return_value=cached)),
        patch.object(
            guest_mod,
            "build_cached_rich_content",
            MagicMock(return_value=("**缓存正文**", [])),
        ),
        patch.object(guest_mod, "ParsePipeline", pipeline_cls),
        patch.object(guest_mod, "_deliver", deliver),
    ):
        ok = asyncio.run(
            guest_mod._answer(
                SimpleNamespace(), "q1", "https://x.com/a/status/1", 1879026273, "zh-hans", caller_msg=None
            )
        )
    return pipeline_cls, deliver, ok


def test_cache_hit_sends_without_running_the_pipeline():
    """命中缓存: 直接用 file_id 发, 绝不启动解析流水线"""
    pipeline_cls, deliver, ok = _run_answer(cached=_cached_entry(), pipeline_should_run=False)

    assert ok is True
    pipeline_cls.assert_not_called()
    deliver.assert_awaited_once()
    kwargs = deliver.await_args.kwargs
    assert kwargs["markdown"] == "**缓存正文**"     # 用的是缓存渲染结果
    assert kwargs["title"] == "标题"


def test_cache_miss_falls_through_to_the_pipeline():
    """没有缓存才走解析"""
    pipeline_cls, _deliver, _ok = _run_answer(cached=None, pipeline_should_run=True)
    pipeline_cls.assert_called_once()
