"""流水线: 富文本是否能跳过下载 / 阶段通知的发法。

**首帧契约（2026-10-09）**: 解析阶段**不发**进度消息 —— 第一条消息就是解析完的
文字排版（`report_result`，页脚带「下载中」）。用户要求「消息首帧取消掉解析中，
直接发解析到的文字结果，然后页脚的下载中处理状态保留」。
"""

import asyncio
from unittest.mock import AsyncMock, patch

from parsehub.types import ImageParseResult, Platform, PostType, RichTextParseResult

from services.pipeline import should_skip_richtext_download


class Article(RichTextParseResult):
    """图片内嵌在正文里 (知乎/微信这类文章)"""


class Attachment(RichTextParseResult):
    """图片被抽成 media 附件 (linux.do 这类帖子)"""

    requires_media_download = True


def test_article_richtext_skips_download():
    assert should_skip_richtext_download(Article(markdown_content="正文"), richtext_skip_download=True)


def test_attachment_richtext_downloads():
    """平台的图片在 media 里, 跳过下载等于把图片丢了"""
    assert not should_skip_richtext_download(Attachment(markdown_content="正文"), richtext_skip_download=True)


def test_switch_off_always_downloads():
    assert not should_skip_richtext_download(Article(), richtext_skip_download=False)


def test_non_richtext_is_unaffected():
    """非富文本结果本来就会下载, 这个判断不该插手"""
    from parsehub.types import ImageParseResult

    assert not should_skip_richtext_download(ImageParseResult(), richtext_skip_download=True)


def test_linuxdo_result_declares_attachment():
    """回归防线: linux.do 的结果类型必须声明需要下载"""
    from parsehub.parsers.parser.linuxdo import LinuxDoRichTextParseResult

    assert LinuxDoRichTextParseResult.requires_media_download is True
    assert LinuxDoRichTextParseResult.type == PostType.RICHTEXT


# ── 阶段通知的首帧（2026-10-09） ──────────────────────────────────────

_URL = "https://x.com/a/status/1"


class _Recorder:
    """记录 reporter 的调用序列（不真发消息）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def report(self, text: str) -> None:
        self.calls.append(("report", text))

    async def report_progress(self, text: str) -> None:
        self.calls.append(("progress", text))

    async def report_result(self, parse_result: object, text: str) -> None:
        self.calls.append(("result", text))

    async def report_error(self, stage: str, error: Exception) -> None:
        self.calls.append(("error", stage))

    async def dismiss(self) -> None:
        self.calls.append(("dismiss", ""))


def _run_pipeline(reporter: _Recorder) -> None:
    """跑一次真实流水线（解析被替身，其余链路真实）。"""
    from services import pipeline as mod

    result = ImageParseResult(content="正文", photo=[])
    with patch.object(mod, "ParseService") as service:
        service.return_value.parse = AsyncMock(return_value=result)
        service.return_value.parser.get_platform.return_value = Platform.TWITTER
        pipe = mod.ParsePipeline(_URL, _URL, reporter, singleflight=False, t=lambda s: s)
        asyncio.run(pipe._execute())


def test_the_parse_stage_sends_no_progress_message():
    """**核心**: 解析阶段一条消息都不发 —— 首帧不再出现「解析中」

    （用户要求「消息首帧取消掉解析中，直接发解析到的文字结果」。）
    """
    reporter = _Recorder()
    _run_pipeline(reporter)
    assert reporter.calls, "流水线没跑起来, 这条断言会空过"
    skeleton_calls = [call for call in reporter.calls if call[0] == "report"]
    assert not skeleton_calls, reporter.calls


def test_the_first_message_is_the_parsed_text_with_the_download_stage():
    """**核心**: 第一条消息 = 解析到的文字排版 + 页脚「下载中」

    （用户要求「直接发解析到的文字结果，然后页脚的下载中处理状态保留」。）
    """
    reporter = _Recorder()
    _run_pipeline(reporter)
    assert reporter.calls[0] == ("result", "下 载 中..."), reporter.calls
    kinds = [call[0] for call in reporter.calls]
    assert kinds.count("result") >= 2, reporter.calls  # 下载中 → 处理中: 阶段推进还在
