import inspect
import logging
import sys
from typing import TYPE_CHECKING, Any

import loguru

if TYPE_CHECKING:
    from loguru import Logger

logger: "Logger" = loguru.logger.bind(name="Main")


def formatter(record: Any) -> str:
    record["extra"].setdefault("name", "UNNAMED")

    rid = record["extra"].get("req_id")
    if rid:
        return (
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> | "
            "<level>[{extra[name]}][{extra[req_id]}] {message}</level>\n"
            "{exception}"
        )
    else:
        return (
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}:{function}:{line}</cyan> | "
            "<level>[{extra[name]}] {message}</level>\n"
            "{exception}"
        )


def setup_logging(debug: bool = False) -> None:
    logger.remove()

    level = "DEBUG" if debug else "INFO"
    logger.add(sys.stderr, level=level, format=formatter)

    logger.add(
        "logs/bot.log",
        rotation="10 MB",
        level="INFO",
        format=formatter,
        enqueue=True,
    )

    if debug:
        logger.debug("调试模式已启用")


#: 库 (`parsehub`) 在 import 时就 **`logger.disable("parsehub")`** 把自己静默了,
#: 而 bot 只在 `bs.debug` 时才 `logger.enable("parsehub")` —— 于是**生产下整个包的
#: 日志都被吞掉**。2026-10-05 排查 pixiv 图床 403 时踩到: 我在下载器里加的诊断
#: warning 一行都没出现, 看起来像"探针没跑"。
#:
#: 只常开**下载相关**模块: 那是一类"用户看到失败、日志里却什么都没有"的问题面;
#: 其余模块保持静默。级别仍由 sink 决定 (INFO), 所以这些模块的 debug 不会刷屏。
_DIAGNOSTIC_MODULES = ("parsehub.utils.downloader", "parsehub.utils.http")


def enable_library_diagnostics() -> None:
    """让库里的关键诊断日志在生产也可见 (见 _DIAGNOSTIC_MODULES 的说明)。"""
    for name in _DIAGNOSTIC_MODULES:
        logger.enable(name)


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[InterceptHandler()], level="ERROR", force=True)
