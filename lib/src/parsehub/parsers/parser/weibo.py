import html
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from ...provider_api.weibo import MediaType, MixMediaInfoItem, PicInfo, WeiboAPI, WeiboTVContent
from ...types import (
    AniRef,
    DownloadResult,
    ImageParseResult,
    ImageRef,
    LivePhotoRef,
    MultimediaParseResult,
    ParseResult,
    Platform,
    ProgressCallback,
    VideoParseResult,
    VideoRef,
)
from ...utils.helpers import image_ext_from_url
from ..base.base import BaseParser

#: 微博的图床（``*.sinaimg.cn``）与视频 CDN（``*.weibocdn.com``）都按 Referer 防盗链:
#: 不带 Referer 的下载**稳定** 403（实测 2026-10-10: 火山引擎 CDN ``deny code 68`` /
#: ``x-ban: MISS`` 响应；带 ``Referer: https://weibo.com`` 同一 URL 立刻 200/206）。
#: 用站点根而不是具体帖子页 —— 实测 ``https://weibo.com`` 与 ``https://m.weibo.cn/``
#: 都可放行，根地址与内容无关、更不易失效。
REFERER = "https://weibo.com"


class WeiboParseResult(ParseResult):
    """微博媒体下载要带 Referer（见 ``REFERER`` 的说明）。"""

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
        headers = {"Referer": REFERER}
        return await super()._do_download(
            output_dir=output_dir,
            callback=callback,
            callback_args=callback_args,
            callback_kwargs=callback_kwargs,
            proxy=proxy,
            headers=headers,
            connections=connections,
        )


class WeiboVideoParseResult(WeiboParseResult, VideoParseResult): ...


class WeiboImageParseResult(WeiboParseResult, ImageParseResult): ...


class WeiboMultimediaParseResult(WeiboParseResult, MultimediaParseResult): ...


class WeiboParser(BaseParser):
    __platform__ = Platform.WEIBO
    __supported_type__ = ["视频", "图文"]
    __match__ = r"^(http(s)?://)((m\.|video\.|)weibo\.(com|cn)/(?!(u/)).+|mapp\.api\.weibo\.cn/fx/.+)"
    __reserved_parameters__ = ["fid"]

    async def _do_parse(
        self, raw_url: str
    ) -> WeiboMultimediaParseResult | WeiboVideoParseResult | WeiboImageParseResult:
        weibo = await WeiboAPI(self.proxy).parse(raw_url)
        if isinstance(weibo, WeiboTVContent):
            return WeiboVideoParseResult(
                content=self.f_text(weibo.text),
                author_name=weibo.author_name,
                video=VideoRef(
                    url=weibo.video_url,
                    thumb_url=weibo.cover_image,
                    duration=int(weibo.video_duration),
                ),
            )

        data = weibo.data
        text = self.f_text(data.content, data.topics)
        media: list[VideoRef | ImageRef | LivePhotoRef | AniRef] = []

        if not data.pic_infos and data.page_info and data.page_info.object_type == MediaType.VIDEO:
            playback = data.page_info.media_info and data.page_info.media_info.playback
            if playback:
                return WeiboVideoParseResult(
                    content=text,
                    author_name=data.author_name,
                    video=VideoRef(
                        url=playback.url,
                        thumb_url=data.page_info.page_pic,
                        width=playback.width,
                        height=playback.height,
                        duration=int(playback.duration),
                    ),
                )

        media_info: list[PicInfo | MixMediaInfoItem] | None = None
        if data.retweeted_status and data.retweeted_status.pic_infos:
            media_info = list(data.retweeted_status.pic_infos)
        elif data.pic_infos:
            media_info = list(data.pic_infos)
        elif data.mix_media_info and data.mix_media_info.items:
            media_info = list(data.mix_media_info.items)
        if not media_info:
            return WeiboMultimediaParseResult(content=text, media=[], author_name=data.author_name)

        for i in media_info:
            match i.type:
                case MediaType.VIDEO:
                    if i.media_url:
                        media.append(
                            VideoRef(
                                url=i.media_url,
                                thumb_url=i.thumb_url,
                                width=i.width,
                                height=i.height,
                                duration=i.duration,
                            )
                        )
                case MediaType.LIVE_PHOTO:
                    if i.thumb_url:
                        media.append(
                            LivePhotoRef(
                                url=i.thumb_url,
                                ext="mov",
                                video_url=i.media_url,
                                width=i.width,
                                height=i.height,
                            )
                        )
                case MediaType.GIF:
                    if i.media_url:
                        media.append(
                            AniRef(
                                url=i.media_url,
                                thumb_url=i.thumb_url,
                                ext=image_ext_from_url(i.media_url, default="gif"),
                            )
                        )
                case _:
                    if i.media_url:
                        media.append(
                            ImageRef(
                                url=i.media_url,
                                thumb_url=i.thumb_url,
                                width=i.width,
                                height=i.height,
                                ext=image_ext_from_url(i.media_url),
                            )
                        )
        if all((isinstance(m, ImageRef) or isinstance(m, LivePhotoRef)) for m in media):
            photos = [m for m in media if isinstance(m, ImageRef | LivePhotoRef)]
            return WeiboImageParseResult(content=text, photo=photos, author_name=data.author_name)
        return WeiboMultimediaParseResult(content=text, media=media, author_name=data.author_name)

    def f_text(self, text: str | None, topics: Sequence[str] | None = None) -> str:
        # text = re.sub(r'<a  href="https://video.weibo.com.*?>.*的微博视频.*</a>', "", text)
        # text = re.sub(r"<[^>]+>", " ", text)
        text = self.hashtag_handler(text or "", topics)
        return text.strip()

    @staticmethod
    def hashtag_handler(desc: str, topics: Sequence[str] | None = None) -> str:
        """把 ``#话题#`` 剥成 ``话题``（保留原有的可见形态，只换边界判据）。

        :param topics: **服务端给的**话题（``Data.topics``: name + 话题页 url）。
            给了就按名字精确匹配 —— 正文里出现**单个** ``#`` 时正则会把不相干的
            一段当成话题（``C# 与 Python# 都`` → 误配成 ``# 与 Python#``）。
            没给（老接口/字段缺失）时退回正则，行为与本参数引入前一致。
        """
        if topics:
            # 用服务端给的话题页地址做成链接（与其它平台的标签形态一致: 保留 `#`
            # 且**可点**）。以前这里是"剥壳成纯文本"——话题不可点, 是唯一与其它
            # 平台不一致的地方。
            named = [t for t in topics if (t or {}).get("name")]
            for topic in sorted(named, key=lambda t: len(t["name"]), reverse=True):
                name = topic["name"]
                url = topic.get("url") or f"https://s.weibo.com/weibo?q=%23{quote(name)}%23"
                desc = desc.replace(
                    f"#{name}#", f'<a href="{html.escape(url, quote=True)}">#{html.escape(name)}#</a>'
                )
            return desc

        hashtags = re.findall(r" ?#[^#]+# ?", desc)
        for hashtag in hashtags:
            desc = desc.replace(hashtag, f" {hashtag.strip().removesuffix('#')} ")
        return desc


__all__ = ["WeiboParser"]
