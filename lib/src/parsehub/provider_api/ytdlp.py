"""yt-dlp 子进程客户端。

``parsers/base/ytdlp.py`` 原先把「调用 yt-dlp」与「解析器骨架」混在一个文件里。这里装的是
**通用基础设施** —— 命令构造、进度行解析、cookie / info JSON 物化、尾部日志与错误提取 ——
与"谁是解析器"无关，平台侧（bilibili/facebook/snapchat）与 YouTube 都通过它取数：

- ``extract_info(url, cli_args, ...)``：``--dump-single-json`` 拿视频信息（解析阶段）
- ``download_video(info_json, cli_args, ...)``：``--load-info-json`` 下载（下载阶段，
  带进度回调；callback 为 None 时不注入 progress 模板）

两者失败一律抛 ``RuntimeError``（信息里带 yt-dlp 的尾部输出），由解析器层翻译成 ``ParseError``。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from loguru import logger

from ..types.callback import ProgressCallback

# 用一个不会和 yt-dlp 普通日志冲突的前缀标记进度行，stdout/stderr 读取时只解析这类行。
PROGRESS_PREFIX = "__PARSEHUB_YTDLP_PROGRESS__"

# yt-dlp CLI 进度模板, download: 是 yt-dlp 的模板作用域前缀；
# 后续字段用 tab 分隔，便于还原成 progress_hooks 风格的 dict。
PROGRESS_TEMPLATE = (
    f"download:{PROGRESS_PREFIX}"
    "%(progress.status)s\t"
    "%(progress.downloaded_bytes)s\t"
    "%(progress.total_bytes)s\t"
    "%(progress.total_bytes_estimate)s\t"
    "%(progress.fragment_index)s\t"
    "%(progress.fragment_count)s"
)

# 子进程失败时只保留尾部日志用于错误信息，避免长输出占用过多内存或污染异常文本。
TAIL_LINES = 100
TAIL_CHARS = 16_000


class MonotonicDownloadProgress:
    def __init__(self, *, start: float = 0.0, end: float = 100.0, min_step: float = 1.0) -> None:
        self.start = start
        self.end = end
        self.min_step = max(1, int(min_step))
        self.current = int(start)

    def update(self, d: dict[str, Any]) -> int | None:
        status = d.get("status")

        if status == "downloading":
            percent = self._download_percent(d)
            if percent is None:
                return None

            mapped = int(self.start + percent * (self.end - self.start) / 100)

            if mapped >= self.current + self.min_step:
                self.current = mapped
                return self.current

        elif status == "finished" and self.current < int(self.end):
            self.current = int(self.end)
            return self.current

        return None

    @staticmethod
    def _download_percent(d: dict[str, Any]) -> float | None:
        downloaded = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if downloaded == total == 1024:
            return None

        if total > 0:
            return min(downloaded / total * 100, 100)

        # 分片下载有时没有稳定总大小，但有 frag 进度；作为兜底
        frag_index = d.get("fragment_index")
        frag_count = d.get("fragment_count")
        if isinstance(frag_index, int | float) and isinstance(frag_count, int | float) and frag_count:
            return min(float(frag_index) / float(frag_count) * 100, 100.0)

        return None


def _yt_dlp_base_cmd() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def _subprocess_kwargs() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    return {}


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:  # pylint: disable=no-member  # asyncio.subprocess.Process 确实存在, pylint 解析不到
    if proc.returncode is not None:
        return

    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            proc.terminate()
    else:
        proc.terminate()

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
        return
    except TimeoutError:
        pass

    if proc.returncode is not None:
        return

    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:
            proc.kill()
    else:
        proc.kill()
    await proc.wait()


@contextmanager
def _temporary_text_file(content: str, *, suffix: str) -> Iterator[str]:
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=suffix) as f:
            path = f.name
            f.write(content)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug("删除 yt-dlp 临时文件失败: {}", e)


@contextmanager
def _materialize_cookie(cookie_text: str | None) -> Iterator[list[str]]:
    if not cookie_text:
        yield []
        return

    with _temporary_text_file(cookie_text, suffix=".cookies.txt") as path:
        yield ["--cookies", path]


@contextmanager
def _materialize_info_json(info_json: dict[str, Any]) -> Iterator[str]:
    content = json.dumps(info_json, ensure_ascii=False)
    with _temporary_text_file(content, suffix=".info.json") as path:
        yield path


def _format_tail(tail: deque[str]) -> str:
    return "".join(tail)[-TAIL_CHARS:].strip()


def _tail_from_text(text: str) -> deque[str]:
    return deque(text.splitlines(keepends=True)[-TAIL_LINES:], maxlen=TAIL_LINES)


def _ytdlp_error(returncode: int, stdout_tail: deque[str], stderr_tail: deque[str]) -> str:
    detail = _format_tail(stderr_tail) or _format_tail(stdout_tail) or "未知错误"
    return f"yt-dlp exited with code {returncode}: {detail}"


def _decode_output(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise RuntimeError("yt-dlp 未输出 JSON")

    try:
        return cast(dict[str, Any], json.loads(text))
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return cast(dict[str, Any], json.loads(text[start : end + 1]))


def _optional_number(value: str) -> int | float | None:
    value = value.strip()
    if not value or value in {"NA", "None", "none", "null"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _parse_progress_line(line: str) -> dict[str, Any] | None:
    index = line.find(PROGRESS_PREFIX)
    if index == -1:
        return None

    payload = line[index + len(PROGRESS_PREFIX) :].strip()
    parts = payload.split("\t")
    if len(parts) < 6:
        return None

    return {
        "status": parts[0],
        "downloaded_bytes": _optional_number(parts[1]),
        "total_bytes": _optional_number(parts[2]),
        "total_bytes_estimate": _optional_number(parts[3]),
        "fragment_index": _optional_number(parts[4]),
        "fragment_count": _optional_number(parts[5]),
    }


async def extract_info(
    url: str,
    cli_args: list[str],
    *,
    proxy: str | None = None,
    cookie_text: str | None = None,
) -> dict[str, Any]:
    with _materialize_cookie(cookie_text) as cookie_args:
        argv = [
            *_yt_dlp_base_cmd(),
            *cli_args,
            *cookie_args,
        ]
        if proxy:
            argv.extend(["--proxy", proxy])
        argv.append(url)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            await _terminate_process(proc)
            raise

    stdout_text = _decode_output(stdout)
    stderr_text = _decode_output(stderr)
    if proc.returncode:
        raise RuntimeError(_ytdlp_error(proc.returncode, _tail_from_text(stdout_text), _tail_from_text(stderr_text)))

    try:
        return _json_from_stdout(stdout_text)
    except Exception as e:
        detail = stderr_text.strip() or str(e)
        raise RuntimeError(f"解析 yt-dlp JSON 失败: {detail}") from e


async def _read_ytdlp_stream(
    stream: asyncio.StreamReader,
    tail: deque[str],
    progress: MonotonicDownloadProgress | None,
    callback: ProgressCallback | None,
    callback_args: tuple,
    callback_kwargs: dict,
) -> None:
    while line := await stream.readline():
        text = _decode_output(line)
        progress_data = _parse_progress_line(text)
        if progress_data and progress and callback:
            count = progress.update(progress_data)
            if count is not None:
                await callback(count, 100, "bytes", *callback_args, **callback_kwargs)
            continue
        tail.append(text)


async def download_video(
    info_json: dict[str, Any],
    cli_args: list[str],
    *,
    outtmpl: str,
    connections: int,
    proxy: str | None = None,
    headers: dict | None = None,
    callback: ProgressCallback | None = None,
    callback_args: tuple = (),
    callback_kwargs: dict | None = None,
) -> None:
    callback_kwargs = callback_kwargs or {}
    stdout_tail: deque[str] = deque(maxlen=TAIL_LINES)
    stderr_tail: deque[str] = deque(maxlen=TAIL_LINES)
    progress = MonotonicDownloadProgress(start=0, end=99) if callback else None

    with _materialize_info_json(info_json) as info_path:
        argv = [*_yt_dlp_base_cmd(), *cli_args]
        if callback:
            argv = [arg for arg in argv if arg not in {"--quiet", "--no-progress"}]
            argv.extend(["--newline", "--progress-template", PROGRESS_TEMPLATE])
        argv.extend(["--load-info-json", info_path, "-o", outtmpl, "-N", str(connections)])
        if proxy:
            argv.extend(["--proxy", proxy])
        for key, value in (headers or {}).items():
            argv.extend(["--add-header", f"{key}: {value}"])

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        if proc.stdout is None or proc.stderr is None:
            await _terminate_process(proc)
            raise RuntimeError("yt-dlp 子进程 stdout/stderr 未正确初始化")

        stdout_task = asyncio.create_task(
            _read_ytdlp_stream(proc.stdout, stdout_tail, progress, callback, callback_args, callback_kwargs)
        )
        stderr_task = asyncio.create_task(
            _read_ytdlp_stream(proc.stderr, stderr_tail, progress, callback, callback_args, callback_kwargs)
        )
        wait_task = asyncio.create_task(proc.wait())

        try:
            returncode = await wait_task
            await asyncio.gather(stdout_task, stderr_task)
        except asyncio.CancelledError:
            await _terminate_process(proc)
            for task in (stdout_task, stderr_task, wait_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
            raise
        except Exception:
            await _terminate_process(proc)
            for task in (stdout_task, stderr_task, wait_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
            raise

    if returncode:
        raise RuntimeError(_ytdlp_error(returncode, stdout_tail, stderr_tail))

__all__ = ["MonotonicDownloadProgress", "download_video", "extract_info"]

