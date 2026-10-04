from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from loguru import logger

from ...provider_api.bilibili import BiliAPI, BiliDynamic, BiliImage
from ...types import (
    DownloadResult,
    ImageParseResult,
    ImageRef,
    LivePhotoRef,
    ParseError,
    Platform,
    ProgressCallback,
    VideoParseResult,
    VideoRef,
)
from ...utils.helpers import UA, format_author_link, format_quote_block, get_author_name, profile_url
from ..base.base import BaseParser
from ..base.ytdlp import YtParser, YtVideoParseResult


class BiliParse(BaseParser):
    __platform__ = Platform.BILIBILI
    __supported_type__ = ["视频", "动态"]
    __match__ = r"^(http(s)?://)?((((w){3}.|(m).|(t).)?bilibili\.com)/(video|opus|\b\d{18,19}\b)|b23.tv|bili2233.cn).*"
    __reserved_parameters__ = ["p"]
    __redirect_keywords__ = ["b23.tv", "bili2233.cn"]

    @staticmethod
    def _to_refs(images: list[BiliImage] | None) -> list[LivePhotoRef | ImageRef]:
        """BiliImage -> 下载用的 Ref (实况照片带视频)。"""
        refs: list[LivePhotoRef | ImageRef] = []
        for i in images or []:
            if i.live_url:
                refs.append(LivePhotoRef(url=i.url, video_url=i.live_url, width=i.width, height=i.height))
            else:
                refs.append(ImageRef(url=i.url, width=i.width, height=i.height))
        return refs

    @staticmethod
    def _strip_forward_comment(content: str) -> str:
        """去掉转发格式里 ``//@原作者:原文`` 那段。

        转发动态的正文是「转发者的话 + ``//@原作者:被转发的原文``」; 原文会由引用块
        重新渲染 (还带原作者署名), 留在正文里就是重复。
        """
        if not content:
            return content
        # 取第一个 //@ 之前的作为转发者自己的评论 (多层转发时同样只留最外层评论)
        stripped = re.split(r"\s*\u200b?\s*//@", content, maxsplit=1)[0]
        return stripped.strip()

    @classmethod
    def _render_forward(cls, forward: BiliDynamic) -> str:
        """把被转发的原动态渲染成引用块 (作者带主页链接, 内容取标题或正文)。

        **被转发的是视频时, 标题链到视频页** —— 引用块里只有封面图, 没有视频本身,
        标题不可点就等于看得到标题、进不去视频 (用户明确要求)。
        """
        raw_title = (forward.title or "").strip()
        title = raw_title
        if raw_title and (url := forward.video_url):
            title = f'<a href="{url}">{raw_title}</a>'
        text = (forward.content or "").strip()
        if title and text and text != raw_title:
            body = f"{title}\n{text}"
        else:
            body = title or text
        if not body and not forward.images:
            return ""
        author = format_author_link(
            forward.author_name or "",
            "",
            profile_url(Platform.BILIBILI, user_id=forward.author_mid),
        )
        return format_quote_block(body, author)

    async def _do_parse(self, raw_url: str) -> YtVideoParseResult | BiliVideoParseResult | ImageParseResult:
        if await self.is_dynamic(raw_url):
            dynamic = await self.get_dynamic_info(raw_url)
            content = self.hashtag_handler(dynamic.content or "")
            photos: list[LivePhotoRef | ImageRef] = []
            photos.extend(BiliParse._to_refs(dynamic.images))

            # 转发动态: 被转发的原动态渲染成引用块 (文字 + 它自己的媒体)
            quoted_media_count = 0
            if forward := dynamic.forward:
                content = BiliParse._strip_forward_comment(content)
                if quote := BiliParse._render_forward(forward):
                    content = f"{content}\n\n{quote}" if content else quote
                forward_refs = BiliParse._to_refs(forward.images)
                quoted_media_count = len(forward_refs)
                photos.extend(forward_refs)

            return ImageParseResult(
                title=dynamic.title or "",
                author_name=dynamic.author_name,
                # B 站没有 @用户名, 主页靠 UID: 名字本身会渲染成 space 链接
                author_url=profile_url(Platform.BILIBILI, user_id=dynamic.author_mid),
                content=content,
                photo=photos,
                published_at=dynamic.published_at,
                like_count=dynamic.like_count,
                quoted_media_count=quoted_media_count,
            )
        else:
            try:
                return await self.bili_api_parse(raw_url)
            except Exception as e:
                logger.opt(exception=e).warning("Bilibili API 解析失败, 尝试 yt-dlp 解析")
                try:
                    return await self.ytp_parse(raw_url)
                except Exception as e:
                    raise ParseError("Bilibili 解析失败") from e

    @staticmethod
    def _is_bvid(url: str) -> bool:
        if url.lower().startswith("bv"):
            return True
        else:
            return False

    @classmethod
    def match(cls, text: str) -> bool:
        if cls._is_bvid(text):
            return True
        else:
            return super().match(text)

    async def get_raw_url(self, url: str, **kwargs: Any) -> str:
        """获取原始链接"""
        if self._is_bvid(url):
            return f"https://www.bilibili.com/video/{url}"
        else:
            raw_url = await super().get_raw_url(url, **kwargs)
            u = urlparse(raw_url)
            q = urlencode([(k, v) for k, v in parse_qsl(u.query) if (k, v) != ("p", "1")])
            return u._replace(query=q).geturl()

    @staticmethod
    async def is_dynamic(url: str) -> str | None:
        """是动态"""
        if re.search(r"\b\d{18,19}\b", url):
            return url
        return None

    async def get_dynamic_info(self, url: str) -> BiliDynamic:
        try:
            async with BiliAPI(proxy=self.proxy) as bili:
                dynamic_info = await bili.get_dynamic_info(url, cookie=self.cookie.get_value())
        except Exception as e:
            if "风控" in str(e):
                raise ParseError(f"账号风控\n使用的cookie: {self.cookie}") from e
            raise ParseError(str(e)) from e
        else:
            return cast(BiliDynamic, dynamic_info)

    async def bili_api_parse(self, url: str) -> BiliVideoParseResult | ImageParseResult:
        async with BiliAPI(proxy=self.proxy) as bili:
            video_info = await bili.get_video_info(url, cookie=self.cookie.get_value())

            if not (data := video_info.get("data")):
                raise ParseError("获取视频信息失败")

            p = int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
            view = data["View"]

            cid = view["cid"]
            duration = view["duration"]
            dimension = view["dimension"]
            desc = view["desc"]

            if p != 1 and (pages := view.get("pages")):
                if page_info := next((i for i in pages if i["page"] == p), None):
                    cid = page_info["cid"]
                    duration = page_info["duration"]
                    dimension = page_info["dimension"]

            b3, b4 = await bili.get_buvid()
            video_playurl = await bili.get_video_playurl(url, cid, b3, b4)

        durl = video_playurl["data"]["durl"][0]
        video_url = self.change_source(durl["backup_url"][0]) if durl.get("backup_url") else durl["url"]
        content = desc.strip()
        if content == "-":
            content = ""
        owner = view.get("owner") or {}
        return BiliVideoParseResult(
            title=data["View"]["title"],
            author_name=get_author_name(owner),
            # B 站没有 @用户名, 主页靠 UID: 名字本身会渲染成 space 链接
            author_url=profile_url(Platform.BILIBILI, user_id=owner.get("mid")),
            content=content,
            published_at=view.get("pubdate"),
            view_count=(view.get("stat") or {}).get("view"),
            video=VideoRef(
                url=video_url,
                thumb_url=data["View"]["pic"],
                duration=duration,
                width=dimension.get("width", 0),
                height=dimension.get("height", 0),
            ),
        )

    async def ytp_parse(self, url: str) -> YtVideoParseResult:
        return await BiliYtParse(proxy=self.proxy, cookie=self.cookie)._do_parse(url)

    @staticmethod
    def change_source(url: str) -> str:
        return re.sub(
            r"upos-.*.(bilivideo.com|mirrorakam.akamaized.net)",
            "upos-sz-upcdnbda2.bilivideo.com",
            url,
        )

    @staticmethod
    def hashtag_handler(desc: str) -> str:
        if not desc:
            return ""
        hashtags = re.findall(r" ?#[^#]+# ?", desc)
        for hashtag in hashtags:
            desc = desc.replace(hashtag, f" {hashtag.strip().removesuffix('#')} ")
        return desc.strip()


class BiliYtParse(YtParser, register=False):
    @property
    def _video_parse_result_type(self) -> type[BiliYtVideoParseResult]:
        return BiliYtVideoParseResult


class BiliYtVideoParseResult(YtVideoParseResult):
    @property
    def cli_args(self) -> list[str]:
        return [
            *super().cli_args,
            "-S",
            "+codec:h264,lang,filesize~500M",
        ]


class BiliVideoParseResult(VideoParseResult):
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
    ) -> DownloadResult:
        headers = {"referer": "https://www.bilibili.com", "User-Agent": UA}
        return await super()._do_download(
            output_dir=output_dir,
            callback=callback,
            callback_args=callback_args,
            callback_kwargs=callback_kwargs,
            proxy=proxy,
            headers=headers,
            connections=connections,
        )


__all__ = [
    "BiliParse",
    "BiliVideoParseResult",
]
