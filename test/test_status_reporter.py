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

    def __init__(self, text="上 传 中...", msg_id=1):
        self.text = text
        self.id = msg_id
        self.edit_text = AsyncMock()
        self.delete = AsyncMock()
        self.answer = AsyncMock()


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


def _no_preview(kwargs) -> bool:
    """这些调用是否带了「关掉链接预览」的参数"""
    opts = kwargs.get("link_preview_options")
    return opts is not None and getattr(opts, "is_disabled", False) is True


def test_dismiss_edits_instead_of_deleting():
    """核心契约: 收尾是编辑, 不是删除"""
    msg = _Msg()
    asyncio.run(_reporter(msg).dismiss())
    msg.edit_text.assert_awaited_once()
    assert msg.edit_text.await_args.args[0] == STATUS_DONE_TEXT
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


def test_finalize_edits_the_status_message():
    """收尾的另一种形态: 直接编辑成最终富文本 (整个流程一条消息)"""
    from pyrogram.types import InputRichMessage

    msg = _Msg()
    rich = InputRichMessage(markdown="结果")
    edited = asyncio.run(_reporter(msg).finalize(rich))
    msg.edit_text.assert_awaited_once()
    assert msg.edit_text.await_args.kwargs["rich_message"] is rich
    assert _no_preview(msg.edit_text.await_args.kwargs)
    msg.delete.assert_not_awaited()
    assert edited is not None


def test_finalize_without_a_status_message_returns_none():
    """没有状态消息 (noprogress) 时返回 None, 让调用方新建"""
    assert asyncio.run(_reporter(None).finalize(object())) is None


def test_finalize_does_not_swallow_errors():
    """编辑失败必须上抛: 调用方要据此回退新建, 不能静默丢结果"""
    msg = _Msg()
    msg.edit_text.side_effect = RuntimeError("MESSAGE_ID_INVALID")
    try:
        asyncio.run(_reporter(msg).finalize(object()))
    except RuntimeError:
        pass
    else:
        raise AssertionError("finalize 应上抛异常")


def test_finalize_text_edits_plain_caption():
    """不走富文本的结果 (GIF 过多只发文字) 也编辑进同一条消息"""
    msg = _Msg()
    asyncio.run(_reporter(msg).finalize_text("<b>文字结果</b>", reply_markup=None))
    msg.edit_text.assert_awaited_once()


# ── 链接预览一律关掉 ───────────────────────────────────────────────────
#
# 回归: 文本结果里带来源链接时, Telegram 会在消息下挂一张 link preview 卡片。
# 卡片在正文之外, **折叠块盖不住它** —— 内容遮住了, 封面图还露着
# (用户报「处理过程占位消息把preview关了, 不然还是能看到图」)。


def test_progress_updates_disable_the_link_preview():
    msg = _Msg(text="解 析 中...")
    asyncio.run(_reporter(msg).report("下 载 中..."))
    assert _no_preview(msg.edit_text.await_args.kwargs)


def test_dismiss_disables_the_link_preview():
    msg = _Msg()
    asyncio.run(_reporter(msg).dismiss())
    assert _no_preview(msg.edit_text.await_args.kwargs)


def test_finalize_text_disables_the_link_preview():
    """这条最容易漏: 文本结果里的来源链接就是预览卡片的来源"""
    msg = _Msg()
    asyncio.run(
        _reporter(msg).finalize_text('<b>正文</b> · <a href="https://x.com/a/status/1">来源</a>')
    )
    assert _no_preview(msg.edit_text.await_args.kwargs)


def test_sending_a_text_message_disables_the_preview_by_default():
    """首次状态消息是**发送**而不是编辑 —— ``MessageSender.text`` 的默认值也要安全

    这个默认值就是接口: 忘了传参数的调用点 (提示语、GIF 提示) 不该泄露预览。
    """
    from plugins.parse.sender import MessageSender

    msg = _Msg()
    sender = MessageSender(
        cli=SimpleNamespace(), msg=msg, config=SimpleNamespace(reply_msg=False)
    )
    asyncio.run(sender.text("解 析 中..."))
    assert _no_preview(msg.answer.await_args.kwargs)


def test_an_explicit_preview_option_is_not_overridden():
    """调用方显式传了的就用调用方的 —— 默认值只补缺, 不覆盖"""
    from pyrogram.types import LinkPreviewOptions

    msg = _Msg()
    allow = LinkPreviewOptions(is_disabled=False)
    asyncio.run(_reporter(msg).finalize_text("正文", link_preview_options=allow))
    assert msg.edit_text.await_args.kwargs["link_preview_options"] is allow


def test_report_survives_a_manually_deleted_status_message():
    """用户手动删掉进度消息后, 后续更新不能打断解析"""
    msg = _Msg()
    msg.edit_text.side_effect = RuntimeError("MESSAGE_ID_INVALID")
    reporter = _reporter(msg)
    asyncio.run(reporter.report("处 理 中..."))  # 不抛即通过
