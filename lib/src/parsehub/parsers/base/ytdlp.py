"""基于 yt-dlp 的解析器基类。

调用 yt-dlp 的那部分（子进程、进度行、cookie / info JSON 物化）已抽到
``provider_api/ytdlp.py`` —— 那是通用基础设施；本文件只保留解析器骨架：
``YtParser``（BaseParser 实现）、``YtVideoParseResult``（下载实现）、
``YtVideoInfo``（yt-dlp info JSON 的字段封装）。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...provider_api.ytdlp import download_video, extract_info
from ...types import (
    AnyParseResult,
    DownloadError,
    DownloadResult,
    ParseError,
    ProgressCallback,
    VideoFile,
    VideoParseResult,
    VideoRef,
)
from ...utils.helpers import get_author_name, to_datetime, to_int
from .base import BaseParser


class YtParser(BaseParser, register=False):
    """yt-dlp解析器"""

    async def _do_parse(self, raw_url: str) -> AnyParseResult:
        video_info = await self._parse(raw_url)
        return self._video_parse_result_type(
            dl=video_info,
            title=video_info.title,
            content=video_info.description,
            author_name=video_info.author_name,
            video=VideoRef(
                url=raw_url,
                thumb_url=video_info.thumbnail,
                width=video_info.width,
                height=video_info.height,
                duration=video_info.duration,
            ),
        )

    @property
    def _video_parse_result_type(self) -> type["YtVideoParseResult"]:
        return YtVideoParseResult

    async def _parse(self, url: str) -> "YtVideoInfo":
        try:
            dl = await asyncio.wait_for(self._extract_info(url), timeout=30)
        except TimeoutError as e:
            raise ParseError("解析视频信息超时") from e
        except Exception as e:
            raise ParseError(f"解析视频信息失败: {str(e)}") from e

        if dl.get("live_status") in (
            "is_live",
            "is_upcoming",
            "was_live",
        ):
            raise ParseError("不支持直播")

        if dl.get("_type") == "playlist":
            entries = dl.get("entries") or []
            if not entries:
                raise ParseError("解析视频信息失败: playlist entries is empty")
            dl = entries[0]
            url = dl.get("webpage_url") or url
        title = dl["title"]
        duration = dl.get("duration", 0)
        # facebook 等站点的条目可能没有 thumbnail / description, 用下标会直接 KeyError
        thumbnail = dl.get("thumbnail", "")
        description = dl.get("description", "")
        width = dl.get("width", 0)
        height = dl.get("height", 0)
        return YtVideoInfo(
            title=title,
            description=description,
            thumbnail=thumbnail,
            duration=duration,
            url=url,
            width=width,
            height=height,
            info_json=dl,
        )

    async def _extract_info(self, url: str) -> dict[str, Any]:
        return await extract_info(
            url,
            self.cli_args,
            proxy=self.proxy,
            cookie_text=self.get_cookie_text(),
        )

    def get_cookie_text(self) -> str | None:
        return None

    @property
    def cli_args(self) -> list[str]:
        return [
            "--quiet",  # 不输出日志
            "--no-progress",  # 不输出下载进度
            "--no-playlist",
            "--dump-single-json",
            "--no-download",
            "--no-warnings",
        ]


class YtVideoParseResult(VideoParseResult):
    def __init__(
        self,
        dl: "YtVideoInfo",
        title: str = "",
        video: VideoRef | None = None,
        content: str = "",
        author_name: str = "",
    ):
        """dl: yt-dlp解析结果"""
        self.dl = dl
        super().__init__(
            title=title,
            video=video,
            content=content,
            author_name=author_name or dl.author_name,
            published_at=dl.published_at,
            view_count=dl.view_count,
            # 作者行的 `@handle` 与主页链接也来自 yt-dlp 的元数据 —— 以前**没传**,
            # 于是 YouTube 的作者行只有名字、没有 @用户名, 也没主页链接, 与其它平台不一致。
            author_handle=dl.author_handle,
            author_url=dl.author_url,
        )

    @property
    def cli_args(self) -> list[str]:
        return [
            "--quiet",  # 不输出日志
            "--no-progress",  # 不输出下载进度
        ]

    async def _do_download(
        self,
        *,
        output_dir: Path,
        callback: ProgressCallback | None = None,
        callback_args: tuple = (),
        callback_kwargs: dict | None = None,
        proxy: str | None = None,
        headers: dict | None = None,
        connections: int = 4,
    ) -> "DownloadResult":
        if callback_kwargs is None:
            callback_kwargs = {}
        output_dir_path = Path(output_dir)

        cli_args = self.cli_args.copy()
        outtmpl = f"{output_dir_path.joinpath(self.name)}.%(ext)s"

        await self._run_download(
            cli_args,
            outtmpl=outtmpl,
            connections=connections,
            proxy=proxy,
            headers=headers,
            callback=callback,
            callback_args=callback_args,
            callback_kwargs=callback_kwargs,
        )

        v = [p for p in output_dir_path.glob(f"{self.name}.*") if p.is_file()]
        if not v:
            raise DownloadError("下载失败: 未找到下载后的视频文件")

        if callback:
            await callback(100, 100, "bytes", *callback_args, **callback_kwargs)

        video_path = v[0]
        return DownloadResult(
            VideoFile(
                path=str(video_path),
                height=self.dl.height,
                width=self.dl.width,
                duration=self.dl.duration,
            ),
            output_dir,
        )

    async def _run_download(
        self,
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
        try:
            await download_video(
                self.dl.info_json,
                cli_args,
                outtmpl=outtmpl,
                connections=connections,
                proxy=proxy,
                headers=headers,
                callback=callback,
                callback_args=callback_args,
                callback_kwargs=callback_kwargs,
            )
        except Exception as e:
            raise DownloadError(f"下载失败: {str(e)}") from e


@dataclass
class YtVideoInfo:
    """raw_video_info: yt-dlp解析结果"""

    title: str
    description: str
    thumbnail: str
    url: str
    info_json: dict[str, Any]
    duration: int = 0
    width: int = 0
    height: int = 0

    @property
    def author_name(self) -> str:
        return get_author_name(self.info_json, "uploader", "channel", "creator", "uploader_id", "channel_id")

    @property
    def published_at(self) -> datetime | None:
        """yt-dlp 的 timestamp (unix 秒); release_timestamp 是首播时间, 作为兜底"""
        return to_datetime(self.info_json.get("timestamp") or self.info_json.get("release_timestamp"))

    @property
    def view_count(self) -> int | None:
        """yt-dlp 的 view_count; 不同站点可用性不一 (facebook 实测有, 点赞/评论通常没有)"""
        return to_int(self.info_json.get("view_count"))

    @property
    def author_handle(self) -> str:
        """作者的 ``@handle``（不含 ``@``, 由上层统一加）。

        取 ``uploader_id``: 新视频是 ``@BlueArchive_JP`` 这种 handle 形态, 老视频是用户名
        (``JoHannesWingsuit``), 两者去掉 ``@`` 后都能直接拼 ``youtube.com/@<handle>``。
        都没有就退回空串 —— 作者行会只显示名字（与拿不到 handle 的其它平台一致）。
        """
        return str(self.info_json.get("uploader_id") or "").lstrip("@").strip()

    @property
    def author_url(self) -> str:
        """作者主页。

        直接用 yt-dlp 给的 ``uploader_url`` / ``channel_url``, **不自己拼模板**:
        ``uploader_id`` 缺失时只剩 ``channel_id``(``UC...``), 套 ``youtube.com/@{handle}``
        会拼出一个打不开的地址; 而这两个字段是平台自己给的、一定可打开。
        """
        return str(self.info_json.get("uploader_url") or self.info_json.get("channel_url") or "")
