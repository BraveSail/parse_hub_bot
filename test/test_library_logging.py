"""库日志的可见性: 生产下**下载器的诊断日志必须能出来**。

背景（2026-10-05，排查 pixiv 图床 403）: 我在下载器里加了失败诊断 warning，
部署后复现 403 —— **日志里一行都没有**，看起来像探针没跑。真因是
`lib/src/parsehub/__init__.py` 里 `logger.disable("parsehub")` 把整个库静默了，
而 bot 只在 `bs.debug` 时才 `logger.enable("parsehub")`。

⚠️ 测这个必须**让下载器自己打日志**（用真实失败路径驱动），不能在测试帧里调
`downloader.logger.warning()` —— loguru 记录里的模块名取自**调用帧**，那样名字是测试模块，
根本测不到"该模块被禁用"这件事。

这个放开是**纯副作用**（删掉不会让别的测试变红）—— 本项目因这类调用点被静默删掉
吃过一次亏（inline 摘键盘），所以最后一条用源码断言盯住 bot.py 的调用。
"""

import asyncio
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

from loguru import logger
from parsehub.errors import DownloadError
from parsehub.utils import http
from parsehub.utils.downloader import SegmentDownloader

import log as bot_log

BOT_PY = pathlib.Path(__file__).resolve().parent.parent / "bot.py"


def _drive_a_403() -> None:
    """跑一次必然 403 的下载, 让**下载器自己**发出诊断日志。"""

    async def forbidden(*args, **kwargs):
        response = type(
            "R",
            (),
            {
                "status_code": 403,
                "headers": {"server": "nginx", "via": "f055"},
                "request": type("Q", (), {"headers": {"Referer": "https://www.pixiv.net/"}})(),
                "url": "https://i.pximg.net/img-master/img/x.jpg",
            },
        )()
        raise http.HTTPStatusError("403", response=response)

    dl = SegmentDownloader("https://i.pximg.net/img/x.jpg", "/tmp/x.jpg", max_retries=0)
    with (
        patch.object(dl, "_download_once", side_effect=forbidden),
        patch.object(dl, "_resolve_path", AsyncMock(return_value=pathlib.Path("/tmp/x.jpg"))),
        patch("parsehub.utils.downloader.asyncio.sleep", AsyncMock()),
        unittest.TestCase().assertRaises(DownloadError),
    ):
        asyncio.run(dl.run())


def _capture(level: str = "WARNING") -> tuple[list[str], int]:
    captured: list[str] = []

    def sink(message) -> None:
        captured.append(message.record["message"])

    # 不带 format: 可调用 sink 收到 Message (带 .record); 带 format 会收到字符串
    return captured, logger.add(sink, level=level)


def test_download_diagnostics_are_invisible_while_the_library_is_silent():
    """前提: 库默认静默 —— 不放开就看不到任何下载诊断"""
    logger.disable("parsehub")
    captured, handler_id = _capture()
    try:
        _drive_a_403()
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")
    assert captured == []


def test_enabling_library_diagnostics_surfaces_the_download_warning():
    """放开后, 下载失败的诊断日志出现 (含 URL / 状态 / 响应头)"""
    logger.disable("parsehub")
    bot_log.enable_library_diagnostics()
    captured, handler_id = _capture()
    try:
        _drive_a_403()
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")

    joined = " ".join(captured)
    assert "i.pximg.net" in joined, joined          # 真实被拒的 URL
    assert "403" in joined, joined                  # 状态
    assert "nginx" in joined, joined                # 响应头 (判断 CDN/限流)
    assert "Referer" in joined, joined              # 请求头 (证明 Referer 带上了)


def test_only_the_download_modules_are_enabled():
    """只放开下载相关模块 —— **不要**图省事放开整个 `parsehub`。

    (这里断言白名单本身而不是"其它模块确实静默": 后者要求从那些模块的帧里发日志,
    而在测试里只能从测试帧发 —— loguru 按调用帧取模块名, 那样测不出真状态。)
    """
    assert bot_log._DIAGNOSTIC_MODULES == ("parsehub.utils.downloader", "parsehub.utils.http")


def test_debug_lines_stay_out_of_an_info_sink():
    """放开的是模块、不是级别: info sink 下它的 debug 仍不该出现"""

    def emit_debug() -> None:
        from parsehub.utils import downloader as dl

        dl.logger.debug("这是 debug 噪音")

    logger.disable("parsehub")
    bot_log.enable_library_diagnostics()
    captured, handler_id = _capture(level="INFO")
    try:
        emit_debug()
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")
    assert captured == [], captured


class BotWiringTest(unittest.TestCase):
    """盯住调用点: 这是**纯副作用**的一行, 删掉不影响任何行为测试。"""

    def test_bot_enables_library_diagnostics(self):
        source = BOT_PY.read_text(encoding="utf-8")
        self.assertIn("from log import enable_library_diagnostics", source)
        self.assertIn("enable_library_diagnostics()", source)


if __name__ == "__main__":
    unittest.main()
