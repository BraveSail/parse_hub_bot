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


MID = "BQAAAH8EAACb7f-02gbl1xUz2F4"


def _cli(inline_message_id: str | None = MID):
    """假 client: 占位调用返回带 inline_message_id 的对象 (None 模拟占位发不出去)。"""
    answered = SimpleNamespace(inline_message_id=inline_message_id)
    return SimpleNamespace(answer_guest_query=AsyncMock(return_value=answered))


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
            MagicMock(return_value=("**缓存正文**", [], {})),
        ),
        patch.object(guest_mod, "ParsePipeline", pipeline_cls),
        patch.object(guest_mod, "_deliver", deliver),
    ):
        ok = asyncio.run(
            guest_mod._answer(_cli(), "q1", "https://x.com/a/status/1", 1879026273, "zh-hans")
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
    # 进度载体是开头的占位拿到的 guest 消息 id
    assert deliver.await_args.args[2] == MID


def test_cache_miss_falls_through_to_the_pipeline():
    """没有缓存才走解析"""
    pipeline_cls, _deliver, _ok = _run_answer(cached=None, pipeline_should_run=True)
    pipeline_cls.assert_called_once()


# ── guest 的处理过程 ───────────────────────────────────────────────────
#
# guest 消息就是一条 inline 消息 (SentGuestMessage: "inline message sent by a guest
# bot"), 所以进度载体就是它自己: 先发占位拿 inline_message_id, 再一路编辑到结果。
#
# 以前是在**召唤消息上 reply** 一条状态消息 —— 但 guest 场景 bot 通常不在召唤群里
# (这正是 guest 模式的意义), 那条 reply 发不出去 (实测 400 CHANNEL_PRIVATE),
# 于是 guest 从来没有处理过程、只有结果。用户报的就是这个。


def test_progress_uses_the_placeholder_as_its_carrier():
    """占位拿到的 id 要交进 _deliver, 否则结果没处编辑"""
    _pipeline, deliver, _ok = _run_answer(cached=_cached_entry(), pipeline_should_run=False)
    assert deliver.await_args.args[2] == MID


def test_without_a_placeholder_the_pipeline_gets_a_null_reporter():
    """占位发不出去 (拿不到 id) 时退回静默, 解析照跑 —— 结果不能因此丢"""
    seen: dict = {}

    @asynccontextmanager
    async def fake_session():
        yield None

    ctx = MagicMock()
    ctx.run = AsyncMock(return_value=None)
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)

    def pipeline(*a, **kw):
        seen["reporter"] = a[2]
        return ctx

    deliver = AsyncMock()
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
        patch.object(guest_mod.persistent_cache, "get", AsyncMock(return_value=None)),
        patch.object(guest_mod, "ParsePipeline", pipeline),
        patch.object(guest_mod, "_deliver", deliver),
    ):
        asyncio.run(
            guest_mod._answer(_cli(inline_message_id=None), "q1", "https://x.com/a/status/1", 1, "zh-hans")
        )

    assert isinstance(seen["reporter"], guest_mod._NullReporter)


def test_deliver_edits_the_guest_message():
    """主路径: 编辑 guest 消息本身, 一条消息走完整个流程"""
    edit = AsyncMock()
    cli = SimpleNamespace()
    with patch.object(guest_mod, "edit_inline_rich_message", edit):
        asyncio.run(
            guest_mod._deliver(cli, "q1", MID, title="t", description="d", markdown="正文", media=None)
        )
    edit.assert_awaited_once()
    assert edit.await_args.args[1] == MID
    assert edit.await_args.kwargs["markdown"] == "正文"


def test_deliver_falls_back_when_editing_fails():
    """编辑失败必须退回 answer_guest_query —— 结果不能因为编辑不被接受就丢掉"""
    cli = SimpleNamespace(answer_guest_query=AsyncMock(return_value=SimpleNamespace(inline_message_id="X")))
    with patch.object(guest_mod, "edit_inline_rich_message", AsyncMock(side_effect=RuntimeError("不接受编辑"))):
        asyncio.run(
            guest_mod._deliver(cli, "q1", MID, title="t", description="d", markdown="正文", media=None)
        )
    cli.answer_guest_query.assert_awaited_once()


def test_deliver_without_a_placeholder_goes_straight_to_guest_query():
    """没有占位 id 时直接走 guest 通道 (与改动前的行为一致)"""
    cli = SimpleNamespace(answer_guest_query=AsyncMock(return_value=SimpleNamespace(inline_message_id="X")))
    with patch.object(guest_mod, "edit_inline_rich_message", AsyncMock()) as edit:
        asyncio.run(
            guest_mod._deliver(cli, "q1", None, title="t", description="d", markdown="正文", media=None)
        )
    edit.assert_not_awaited()
    cli.answer_guest_query.assert_awaited_once()
