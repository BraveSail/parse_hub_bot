"""下载速度日志: **实际字节数 / 耗时 / 平均速度**。

起因（2026-10-06，用户）:「你给下载器加个速度log，我就不信有这么快」——
测速只在探针里打，日志里看不到；而这次要的正是**生产日志里的真实速度**，
好在事后核对"到底多快、是不是稳定"。

两个容易做错的地方，都被下面钉住了:

1. **字节数不能用进度回调取** —— `_downloaded` 原来在没有 `progress` 回调时
   **直接早退**，那样速度日志永远是 0（生产调用下载器大多不带回调）。
2. **日志必须真的挂在完成路径上** —— 只测 `_log_speed` 的格式没用，
   所以有一条走完整的 `run()` 成功路径。
"""

import asyncio
import pathlib
from unittest.mock import AsyncMock, patch

from loguru import logger

from parsehub.utils.downloader import RangeProbe, SegmentDownloader

MODULE = "parsehub.utils.downloader"
MB = 1024 * 1024


class _FakeClient:
    """`run()` 里是 `async with self._client() as client`，所以要个异步上下文管理器。"""

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


def _capture(level: str = "INFO") -> tuple[list[str], int]:
    captured: list[str] = []

    def sink(message) -> None:
        captured.append(message.record["message"])

    return captured, logger.add(sink, level=level)


def _run_a_download(tmp_path: pathlib.Path, *, size: int = 2 * MB, connections: int = 1) -> list[str]:
    """跑一次**成功**的下载, 返回下载器自己打出的日志。"""
    target = tmp_path / "out.mp4"
    dl = SegmentDownloader(
        "https://upos-sz-mirrorcosov.bilivideo.com/a/out.mp4",
        str(target),
        max_retries=0,
        connections=connections,
        min_split_size=1,
    )

    async def fake_single(client, total_size):  # noqa: ANN001, ANN202
        dl._require_complete_path().write_bytes(b"x" * size)
        await dl._report_single(size, size)

    captured, handler_id = _capture()
    logger.disable("parsehub")
    logger.enable(MODULE)
    try:
        with (
            patch.object(dl, "_client", lambda: _FakeClient()),
            patch.object(dl, "_resolve_path", AsyncMock(return_value=target)),
            patch.object(dl, "_probe", AsyncMock(return_value=RangeProbe(supports_range=False, total_size=size))),
            patch.object(dl, "_download_single", fake_single),
        ):
            asyncio.run(dl.run())
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")
    return captured


def test_a_finished_download_logs_its_speed(tmp_path: pathlib.Path) -> None:
    """完成路径上确实有一行速度日志（含字节数、平均速度、host）"""
    logged = " ".join(_run_a_download(tmp_path))
    assert "下载完成" in logged, logged
    assert "2.00MB" in logged, logged          # 实际字节数
    assert "平均" in logged, logged
    assert "MB/s" in logged, logged
    assert "upos-sz-mirrorcosov.bilivideo.com" in logged, logged  # host 是速度的主要变量


def test_the_line_is_not_merely_defined_but_actually_emitted(tmp_path: pathlib.Path) -> None:
    """回归: 只定义 `_log_speed` 而忘了在完成路径调用, 以前就会这样漏"""
    assert any("下载完成" in line for line in _run_a_download(tmp_path))


def test_multipart_downloads_report_their_split_count(tmp_path: pathlib.Path) -> None:
    """多分片要打出**分片数**（并发度直接决定速度）"""
    target = tmp_path / "big.mp4"
    dl = SegmentDownloader("https://host/a/big.mp4", str(target))
    dl._part_count = 4
    target.write_bytes(b"x" * (3 * MB))

    captured, handler_id = _capture()
    logger.disable("parsehub")
    logger.enable(MODULE)
    try:
        dl._log_speed(1.0, target)
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")

    logged = " ".join(captured)
    assert "分片 4" in logged, logged
    assert "3.00MB" in logged, logged
    assert "3.00 MB/s" in logged, logged  # 3MB / 1.0s


def test_a_single_stream_reports_one_split(tmp_path: pathlib.Path) -> None:
    """没分片时显示 1（而不是 0）—— 0 会让人以为统计坏了"""
    target = tmp_path / "one.mp4"
    target.write_bytes(b"x" * MB)
    dl = SegmentDownloader("https://host/a/one.mp4", str(target))

    captured, handler_id = _capture()
    logger.disable("parsehub")
    logger.enable(MODULE)
    try:
        dl._log_speed(1.0, target)
    finally:
        logger.remove(handler_id)
        logger.disable("parsehub")

    assert "分片 1" in " ".join(captured)


def test_bytes_are_counted_even_without_a_progress_callback() -> None:
    """**关键回归**: 没有 `progress` 回调时也要记账, 否则速度日志的分母是 0

    生产调用下载器基本不带进度回调（只有 Telegram 上传那条路要进度条）。
    """
    dl = SegmentDownloader("https://host/a/x.mp4", "/tmp/x.mp4")
    assert dl.progress is None

    asyncio.run(dl._report_single(1024, 2048))
    assert dl._downloaded == 1024

    # 多分片: 每个分片各报各的已下字节, 总量按增量累加
    asyncio.run(dl._report_part(0, 512, 2048))
    asyncio.run(dl._report_part(0, 900, 2048))
    asyncio.run(dl._report_part(1, 700, 2048))
    assert dl._downloaded == 1024 + 900 + 700


def test_the_progress_callback_still_fires_when_it_exists() -> None:
    """解耦归解耦, 有回调时行为不能变（进度条还在用）"""
    seen: list[tuple[int, int]] = []

    async def progress(current: int, total: int) -> None:
        seen.append((current, total))

    dl = SegmentDownloader("https://host/a/x.mp4", "/tmp/x.mp4", progress=progress)
    asyncio.run(dl._report_single(500, 1000))
    asyncio.run(dl._report_single(1000, 1000))
    assert seen == [(500, 1000), (1000, 1000)]


def test_a_missing_file_does_not_break_the_logging(tmp_path: pathlib.Path) -> None:
    """文件没了就静默跳过 —— 打日志不能把下载搞挂"""
    dl = SegmentDownloader("https://host/a/gone.mp4", str(tmp_path / "gone.mp4"))
    dl._log_speed(1.0, tmp_path / "gone.mp4")  # 不该抛


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
