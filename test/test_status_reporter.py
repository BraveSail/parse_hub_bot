"""状态消息的收尾: 编辑成最小标记, 而不是删除。

删除会在 Telegram 后台留下"消息已删除"的记录 (用户明确要求避免), 编辑不会。
`dismiss` 还必须吞掉异常 —— 它在 handlers 的 try 块里, 抛错会把已经成功的解析
误判成"上传失败"。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugins.parse.reporters import STATUS_DONE_TEXT, MessageStatusReporter


class _Msg:
    """够用的假 Message: 记录 edit_text / delete 的调用。"""

    def __init__(self, text="上 传 中..."):
        self.text = text
        self.edit_text = AsyncMock()
        self.delete = AsyncMock()


def _reporter(msg=None, *, config=None) -> MessageStatusReporter:
    reporter = MessageStatusReporter(
        cli=SimpleNamespace(),
        user_msg=SimpleNamespace(),
        t=lambda key, **kw: key,
        config=config or SimpleNamespace(noprogress=False, hide_error=False, keep_error_log=True),
    )
    reporter._msg = msg
    return reporter


def test_done_text_is_a_real_visible_character():
    """不能用空串/零宽字符: Telegram 会回 400 MESSAGE_EMPTY (实测过)"""
    assert STATUS_DONE_TEXT
    assert STATUS_DONE_TEXT.strip() == STATUS_DONE_TEXT != ""
    assert not any(ch in STATUS_DONE_TEXT for ch in ("\u200b", "\u200d"))


def test_dismiss_edits_instead_of_deleting():
    """核心契约: 收尾是编辑, 不是删除"""
    msg = _Msg()
    asyncio.run(_reporter(msg).dismiss())
    msg.edit_text.assert_awaited_once_with(STATUS_DONE_TEXT)
    msg.delete.assert_not_awaited()


def test_dismiss_without_a_status_message_does_nothing():
    """noprogress / 首条发送失败时没有状态消息, 不该炸"""
    asyncio.run(_reporter(None).dismiss())


def test_dismiss_swallows_errors():
    """编辑失败也要静默 —— 抛出去会被上层当成上传失败"""
    msg = _Msg()
    msg.edit_text.side_effect = RuntimeError("从服务器得到未知错误")
    asyncio.run(_reporter(msg).dismiss())  # 不抛即通过


def test_report_edits_the_same_message():
    """进度更新是编辑同一条消息 (收尾也走同一条, 所以整个流程只留一条)"""
    msg = _Msg(text="解 析 中...")
    reporter = _reporter(msg)
    asyncio.run(reporter.report("下 载 中..."))
    msg.edit_text.assert_awaited_once()
    assert "下 载 中..." in msg.edit_text.await_args.args[0]
    msg.delete.assert_not_awaited()


def test_report_is_skipped_when_noprogress():
    msg = _Msg()
    reporter = _reporter(msg, config=SimpleNamespace(noprogress=True))
    asyncio.run(reporter.report("下 载 中..."))
    msg.edit_text.assert_not_awaited()


def test_report_survives_a_manually_deleted_status_message():
    """用户手动删掉进度消息后, 后续更新不能打断解析"""
    msg = _Msg()
    msg.edit_text.side_effect = RuntimeError("MESSAGE_ID_INVALID")
    reporter = _reporter(msg)
    asyncio.run(reporter.report("处 理 中..."))  # 不抛即通过
