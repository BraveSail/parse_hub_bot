"""下载重试策略: 403/429 算限流、退避要够长、诊断日志要带 URL。

背景（2026-10-05）: pixiv 图床间歇 403（`i.pximg.net`）。排查时**最卡的一步**是
下载器一行日志都没有 —— 失败时只看到 "下载错误: HTTP错误: 403"，不知道是哪个 URL、
图床返回了什么头。而重试侧的问题是退避太短: 403 被判为"不可重试"，真正在重试的是
pipeline 层的 `_step(retries=2)`，退避 1s/2s —— 限流窗口通常几十秒，结构上等不到恢复。
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from parsehub.utils.downloader import (
    _BACKOFF_403,
    _BACKOFF_429,
    _is_rate_limited,
    _is_retryable_status,
    _retry_delay,
)


class RetryableStatusTest(unittest.TestCase):
    def test_403_counts_as_rate_limited(self):
        """图床限流的表现就是 403（与防盗链同一个码）, 必须当可重试处理"""
        self.assertTrue(_is_rate_limited(403))
        self.assertTrue(_is_rate_limited(429))

    def test_other_statuses_are_not_rate_limited(self):
        for status in (400, 401, 404, 500, 502):
            self.assertFalse(_is_rate_limited(status), status)

    def test_a_missing_resource_is_not_retryable(self):
        """404/400 重试没有意义 —— 别让用户白等"""
        self.assertFalse(_is_retryable_status(404))
        self.assertFalse(_is_retryable_status(400))
        self.assertFalse(_is_retryable_status(403))

    def test_server_errors_stay_retryable(self):
        self.assertTrue(_is_retryable_status(500))
        self.assertTrue(_is_retryable_status(503))
        self.assertTrue(_is_retryable_status(429))


class BackoffTest(unittest.TestCase):
    def test_403_waits_longer_than_a_generic_failure(self):
        """核心: 403 的退避必须比 2**attempt 长, 否则限流窗口没过就重试完 3 次了"""
        for attempt in range(3):
            self.assertGreater(_retry_delay(attempt, status_code=403), float(2**attempt), attempt)

    def test_429_waits_the_longest(self):
        """429 是明确的限流信号, 比 403 更值得等"""
        self.assertGreater(_retry_delay(0, status_code=429), _retry_delay(0, status_code=403))

    def test_403_still_fails_fast_enough(self):
        """403 也可能是**稳定**的拒绝（如 Referer 不对）—— 总等待要有限,
        不能拖到一分钟才把错误报出来。"""
        self.assertLessEqual(sum(_BACKOFF_403), 30)

    def test_the_backoff_is_capped(self):
        """超出序列长度就停在最后一个值 (不会无限增长)"""
        self.assertEqual(_retry_delay(99, status_code=429), _BACKOFF_429[-1])
        self.assertEqual(_retry_delay(99, status_code=403), _BACKOFF_403[-1])

    def test_generic_statuses_keep_the_exponential_backoff(self):
        """普通故障 (5xx) 不被放大 —— 维护原有的 2**attempt 节奏"""
        self.assertEqual([_retry_delay(i, status_code=500) for i in range(3)], [1.0, 2.0, 4.0])

    def test_no_status_falls_back_to_exponential(self):
        self.assertEqual(_retry_delay(2, status_code=None), 4.0)


class RetryAttemptsTest(unittest.TestCase):
    """在下载器层面确认: 403 会被**重试**（而不像以前那样立刻抛出）。"""

    def _downloader(self):
        from parsehub.utils.downloader import SegmentDownloader

        return SegmentDownloader("https://i.pximg.net/img/a.jpg", "/tmp/x.jpg", max_retries=2)

    def test_a_403_is_retried_and_can_succeed(self):
        from parsehub.utils import http

        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                response = type("R", (), {"status_code": 403, "headers": {}})()
                raise http.HTTPStatusError("403", response=response)

        dl = self._downloader()
        with (
            patch.object(dl, "_download_once", side_effect=flaky),
            patch.object(dl, "_resolve_path", AsyncMock(return_value=Path("/tmp/x.jpg"))),
            patch("parsehub.utils.downloader.asyncio.sleep", AsyncMock()) as sleeper,
        ):
            result = asyncio.run(dl.run())

        self.assertEqual(result, "/tmp/x.jpg")
        self.assertEqual(calls["n"], 3, "前两次 403 应重试")
        delays = [c.args[0] for c in sleeper.await_args_list]
        self.assertEqual(delays, [3.0, 10.0], "403 应走专用长退避")

    def test_a_404_is_not_retried(self):
        from parsehub.errors import DownloadError
        from parsehub.utils import http

        async def not_found(*args, **kwargs):
            response = type("R", (), {"status_code": 404, "headers": {}})()
            raise http.HTTPStatusError("404", response=response)

        dl = self._downloader()
        with (
            patch.object(dl, "_download_once", side_effect=not_found),
            patch.object(dl, "_resolve_path", AsyncMock(return_value=Path("/tmp/x.jpg"))),
            patch("parsehub.utils.downloader.asyncio.sleep", AsyncMock()) as sleeper,
        ):
            with self.assertRaises(DownloadError):
                asyncio.run(dl.run())

        sleeper.assert_not_awaited(), "404 不应重试"

    def test_the_failure_log_mentions_the_url_and_the_headers(self):
        """诊断关键: 失败日志必须带 URL + 状态 + 响应头 (排查时缺的正是这些)"""
        from parsehub.errors import DownloadError
        from parsehub.utils import http
        from parsehub.utils.downloader import logger as dl_logger

        async def not_found(*args, **kwargs):
            response = type("R", (), {"status_code": 403, "headers": {"server": "nginx", "via": "f055"}})()
            raise http.HTTPStatusError("403", response=response)

        dl = self._downloader()
        with (
            patch.object(dl, "_download_once", side_effect=not_found),
            patch.object(dl, "_resolve_path", AsyncMock(return_value=Path("/tmp/x.jpg"))),
            patch("parsehub.utils.downloader.asyncio.sleep", AsyncMock()),
            patch.object(dl_logger, "warning") as warning,
        ):
            with self.assertRaises(DownloadError):
                asyncio.run(dl.run())

        said = " ".join(str(c.args[0]) for c in warning.call_args_list)
        self.assertIn("i.pximg.net", said)
        self.assertIn("403", said)
        self.assertIn("nginx", said)


if __name__ == "__main__":
    unittest.main()
