"""YouTube 一个 parser 管两类链接：视频（yt-dlp）与社区帖子（页面数据）。

帖子的解析路径和视频完全不同 —— yt-dlp 把 ``/post/<id>`` 当成频道 tab
（``[youtube:tab] post: This channel does not have a Ugk… tab``），所以帖子只能读
页面里的 ``ytInitialData``（见 ``provider_api/youtube.py``）。两条路径在这里按 URL
分派，对外仍是同一个平台、同一个 parser。
"""

import html

from ...provider_api.youtube import (
    YoutubePostError,
    YoutubePostPoll,
    YoutubePostVideo,
    fetch_post,
    post_id_from_url,
)
from ...types import ImageRef, MultimediaParseResult, ParseError, Platform
from ...utils.helpers import profile_url
from ..base.ytdlp import YtParser, YtVideoParseResult


class YtbParse(YtParser):
    __platform__ = Platform.YOUTUBE
    __supported_type__ = ["视频", "音乐", "图文"]
    # post 要匹配（早期版本把它排除了, 于是帖子落到「不支持的平台」）；
    # ``@handle`` 频道页仍不支持（那种页面拿不到视频本体）。
    __match__ = r"^(http(s)?://).*youtu(be|.be)?(\.com)?/(?!live)(?!@).+"
    __redirect_keywords__ = ["m.youtube.com"]
    __reserved_parameters__ = ["v", "list", "index"]

    @property
    def _video_parse_result_type(self) -> type["YtbVideoParseResult"]:
        return YtbVideoParseResult

    async def _do_parse(self, raw_url: str) -> YtVideoParseResult | MultimediaParseResult:
        """帖子走页面数据, 其余链接（视频/音乐）仍交给 yt-dlp。"""
        if post_id_from_url(raw_url):
            return await self._parse_post(raw_url)
        return await super()._do_parse(raw_url)

    async def _parse_post(self, raw_url: str) -> MultimediaParseResult:
        try:
            post = await fetch_post(
                raw_url,
                proxy=self.proxy,
                cookie=self.cookie.get_value() if self.cookie else None,
            )
        except YoutubePostError as e:
            raise ParseError(f"无法获取帖子内容: {e}") from e
        except Exception as e:  # noqa: BLE001 - 网络/解析异常都要翻译成 ParseError
            raise ParseError(f"无法获取帖子内容: {e}") from e

        media: list[ImageRef] = [
            ImageRef(
                url=image.url,
                thumb_url=image.thumb_url,
                width=image.width,
                height=image.height,
            )
            for image in post.images
        ]
        # 帖子里分享的视频只渲染封面 + 链接, **不下载**（那不是帖子本体）
        if post.video and post.video.cover_url:
            media.append(ImageRef(url=post.video.cover_url, thumb_url=post.video.cover_url))

        blocks = [post.text.strip()]
        if post.poll:
            blocks.append(YtbParse._build_poll(post.poll))
        if post.video:
            blocks.append(YtbParse._build_video_link(post.video))

        return MultimediaParseResult(
            content="\n\n".join(block for block in blocks if block),
            media=media,
            author_name=post.author_name,
            author_handle=post.author_handle,
            author_url=profile_url(Platform.YOUTUBE, post.author_handle),
            published_at=post.published_at,
            like_count=post.like_count,
            hashtags=post.hashtags,
        )

    @staticmethod
    def _poll_cell(text: str) -> str:
        """表格单元格转义 —— 选项文案里的 ``|`` 会把列切断。"""
        return text.replace("|", "\\|")

    @staticmethod
    def _build_poll(poll: YoutubePostPoll) -> str:
        """投票 -> 「总数 + 选项表」。

        与 twitter / linux.do 的投票同为 markdown 表格（渲染层转成服务端 Table 块）。
        只有一列：**每项的票数匿名拿不到**（页面只给 ``signinEndpoint``）, 登录才有。
        """
        header = "投票"
        if poll.total_votes:
            header = f"{header} · 共 {poll.total_votes:,} 票"
        rows = [header, "", "| 选项 |", "| --- |"]
        rows.extend(f"| {YtbParse._poll_cell(choice)} |" for choice in poll.choices)
        return "\n".join(rows)

    @staticmethod
    def _build_video_link(video: YoutubePostVideo) -> str:
        """帖子里分享的视频 -> 正文里的一行链接（封面另作一张图）。

        用 HTML ``<a>`` 而不是 markdown 链接: 正文与引用块两处都有效
        （markdown 链接在引用块里不解析, 同 bilibili 的标签处理）。
        """
        url = f"https://www.youtube.com/watch?v={video.video_id}"
        title = video.title or url
        return f'<a href="{url}">{html.escape(title)}</a>'

    def get_cookie_text(self) -> str | None:
        if cookie := self.cookie.get_value():
            return self.to_netscape_cookie(cookie, "youtube.com")
        return None

    @staticmethod
    def to_netscape_cookie(cookie: dict | None, domain: str) -> str | None:
        """将字典格式 cookie 转为 Netscape 格式字符串
        :param cookie: 字典格式 cookie
        :param domain: cookie 所属域名, 例如 "youtube.com"
        """
        if not cookie:
            return None
        if not domain.startswith("."):
            domain = f".{domain}"
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in cookie.items():
            lines.append(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
        return "\n".join(lines) + "\n"


class YtbVideoParseResult(YtVideoParseResult):
    @property
    def cli_args(self) -> list[str]:
        return [
            *super().cli_args,
            "-S",
            "+codec:h264,lang,filesize~500M",
            # "--write-subs", # 下载字幕
            # "--write-auto-subs", # 下载自动生成的字幕
            # "--sub-format", "ttml", # 字幕格式
            # "--sub-langs", "en,ja,zh-CN", # 字幕语言
        ]


__all__ = ["YtbParse"]
