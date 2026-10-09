import contextlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from parsehub.errors import DownloadError
from parsehub.utils.downloader import download


class RangeTestHandler(BaseHTTPRequestHandler):
    content: ClassVar[bytes] = b""
    support_range: ClassVar[bool] = True
    fail_all: ClassVar[bool] = False
    requests: ClassVar[list[tuple[str, str | None]]] = []
    #: 只发到文件的这个偏移就断连（模拟 CDN 在固定位置 `curl: (18)`）。
    #: None = 正常发完。这是**缺陷 2** 的最小复现装置。
    truncate_at: ClassVar[int | None] = None

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_HEAD(self) -> None:
        self.__class__.requests.append(("HEAD", self.headers.get("Range")))
        if self.fail_all:
            self.send_response(500)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self.end_headers()

    def _write_truncated(self, body: bytes, start: int) -> None:
        """按 ``truncate_at`` 截断后写入，然后**关掉连接**（模拟服务端提前断开）。

        关键：Content-Length / Content-Range 报的是**完整长度**，实际只给一部分 ——
        这正是 CDN 那份坏副本的行为（客户端拿 curl 18 / IncompleteRead）。
        """
        limit = self.__class__.truncate_at
        if limit is None:
            self.wfile.write(body)
            return
        allowed = max(0, limit - start)
        if allowed:
            self.wfile.write(body[:allowed])
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        self.__class__.requests.append(("GET", range_header))
        if self.fail_all:
            self.send_response(500)
            self.end_headers()
            return

        if range_header and self.support_range:
            start, end = self._parse_range(range_header)
            if start >= len(self.content):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(self.content)}")
                self.end_headers()
                return

            end = min(end, len(self.content) - 1)
            body = self.content[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.content)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self._write_truncated(body, start)
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self._write_truncated(self.content, 0)

    @staticmethod
    def _parse_range(header: str) -> tuple[int, int]:
        """解析 Range 头。**支持开放式范围** `bytes=<start>-`（到结尾）。

        需要它才能覆盖"整段 Range"这条路径：有些 CDN 拒绝不带 Range 的整段请求
        （googlevideo 的 `rqh=1` URL），所以下载器对整段也发 `bytes=0-`。
        """
        prefix = "bytes="
        if not header.startswith(prefix):
            return 0, 0
        start_text, end_text = header.removeprefix(prefix).split("-", 1)
        if not end_text.strip():
            return int(start_text), 2**63 - 1  # 开放式: 到结尾（调用方会按内容长度收窄）
        return int(start_text), int(end_text)


@contextlib.contextmanager
def range_server(*, content: bytes, support_range: bool = True, fail_all: bool = False, truncate_at: int | None = None):
    class Handler(RangeTestHandler):
        pass

    Handler.content = content
    Handler.support_range = support_range
    Handler.fail_all = fail_all
    Handler.truncate_at = truncate_at
    Handler.requests = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/file.bin", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DownloaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_download_uses_range_parts_when_server_supports_range(self):
        content = bytes(range(251)) * 20
        progresses: list[tuple[int, int]] = []

        async def progress(current: int, total: int) -> None:
            progresses.append((current, total))

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=True) as (url, handler):
            target = Path(tmp) / "video.bin"

            path = await download(
                url,
                target,
                progress=progress,
                connections=4,
                min_split_size=512,
                chunk_size=128,
            )

            self.assertEqual(Path(path), target)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(progresses[-1], (len(content), len(content)))
            range_gets = [range_header for method, range_header in handler.requests if method == "GET" and range_header]
            self.assertGreaterEqual(len(range_gets), 2)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_download_falls_back_to_single_request_when_range_is_ignored(self):
        content = b"fallback-body" * 100

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=False) as (url, handler):
            target = Path(tmp) / "image.bin"

            await download(url, target, connections=4, min_split_size=10, chunk_size=32)

            self.assertEqual(target.read_bytes(), content)
            self.assertIn(("GET", "bytes=0-0"), handler.requests)
            # 单连接回退也**带** `bytes=0-`（见下载器的注释：有些 CDN 拒绝无 Range 的整段请求）
            self.assertIn(("GET", "bytes=0-"), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_download_keeps_existing_file_when_request_fails(self):
        with TemporaryDirectory() as tmp, range_server(content=b"new", support_range=True, fail_all=True) as (url, _):
            target = Path(tmp) / "video.bin"
            target.write_bytes(b"old")

            with self.assertRaises(DownloadError):
                await download(url, target, connections=4, max_retries=0)

            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_connections_one_uses_single_request(self):
        content = b"single" * 200

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=True) as (url, handler):
            target = Path(tmp) / "single.bin"

            await download(url, target, connections=1, min_split_size=10)

            self.assertEqual(target.read_bytes(), content)
            range_gets = [range_header for method, range_header in handler.requests if method == "GET" and range_header]
            # **只发一个请求**, 但它是带 `bytes=0-` 的整段 Range —— 不带 Range 的整段请求
            # 会被某些 CDN 直接断连（googlevideo 的 `rqh=1` URL 实测稳定复现）。
            self.assertEqual(range_gets, ["bytes=0-"])

    async def test_truncated_stream_is_retried_and_reported_as_network_error(self):
        """服务端在中途断流（curl 18 / IncompleteRead）时：

        ① 必须被当作**可重试的网络错误** —— 历史实现把它误判成 `HTTPStatusError`，
           于是报出 `分片下载失败: HTTP 206`（一个成功码）并且**一次都不重试**；
        ② 错误信息里**不能出现 206**（那是成功码，只会误导排障）；
        ③ 断流发生时要真的重试过（请求次数 > 分片数）。
        """
        content = bytes(range(251)) * 200  # 50KB，够切成多个分片
        truncate_at = 1024  # 只给 1KB 就断

        with (
            TemporaryDirectory() as tmp,
            range_server(content=content, support_range=True, truncate_at=truncate_at) as (url, handler),
        ):
            target = Path(tmp) / "broken.bin"

            with self.assertRaises(DownloadError) as ctx:
                await download(url, target, connections=4, min_split_size=512, max_retries=2)

            message = str(ctx.exception)
            self.assertNotIn("206", message, f"成功码不该出现在错误信息里: {message!r}")
            # 断流必须触发重试：分片数 + 每次重试都会再发请求
            range_gets = [r for method, r in handler.requests if method == "GET" and r]
            part_count = 4
            self.assertGreater(
                len(range_gets),
                part_count,
                f"断流后应重试（> {part_count} 次 GET），实际 {len(range_gets)} 次",
            )
            # 临时文件必须清干净，目标文件不能是半截的
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))
            self.assertFalse(target.exists())

    async def test_falls_back_to_backup_url_when_primary_truncates(self):
        """主地址在某偏移断流 → 换**备用地址**整体重下，最终文件必须是完整正确的。

        这正是生产故障（B 站 durl.url 指向的那份 Akamai 副本在 9.49MB 处不可读）
        应有的行为：同一份内容换一份副本，而不是把整次下载判死。
        """
        content = bytes(range(251)) * 200

        with TemporaryDirectory() as tmp:
            # 主地址：声明完整长度但只发 1KB 就断
            with range_server(content=content, support_range=True, truncate_at=1024) as (primary_url, primary_handler):
                # 备用地址：正常
                with range_server(content=content, support_range=True) as (backup_url, backup_handler):
                    target = Path(tmp) / "video.bin"

                    path = await download(
                        primary_url,
                        target,
                        connections=4,
                        min_split_size=512,
                        backup_urls=(backup_url,),
                    )

                    self.assertEqual(Path(path), target)
                    self.assertEqual(target.read_bytes(), content)
                    # 备用地址**确实被访问过**（否则这条用例没有证明回退发生）
                    self.assertTrue(
                        any(method == "HEAD" or r for method, r in backup_handler.requests),
                        "备用地址未被请求，回退没有发生",
                    )
                    self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))


if __name__ == "__main__":
    unittest.main()
