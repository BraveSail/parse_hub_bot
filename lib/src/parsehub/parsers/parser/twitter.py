from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse, urlunparse

from ...provider_api.twitter import (
    Twitter,
    TwitterAni,
    TwitterPhoto,
    TwitterTweet,
    TwitterVideo,
)
from ...types import (
    AniRef,
    AnyMediaRef,
    ImageRef,
    MultimediaParseResult,
    ParseError,
    Platform,
    RichTextParseResult,
    VideoRef,
)
from ...utils.helpers import format_author_link, format_quote_block, profile_url
from ..base.base import BaseParser


class TwitterParser(BaseParser):
    __platform__ = Platform.TWITTER
    __supported_type__ = ["视频", "图文"]
    __match__ = r"^(http(s)?://)?.+(twitter|fixupx|x).com/.*/status/\d+"

    async def _do_parse(self, raw_url: str) -> MultimediaParseResult | RichTextParseResult:
        tweet = await self._parse(raw_url)
        return await self.media_parse(tweet)

    async def get_raw_url(self, url: str, **kwargs: Any) -> str:
        url = await super().get_raw_url(url, **kwargs)
        return str(urlunparse(urlparse(url)._replace(netloc="x.com")))

    async def _parse(self, url: str) -> TwitterTweet:
        x = Twitter(self.proxy, cookie=None)
        try:
            tweet = await x.fetch_tweet(url)
        except Exception as e:
            if any(s in str(e) for s in ("error -2", "error -3")):
                if cookie := self.cookie.get_value():
                    x2 = Twitter(self.proxy, cookie=cookie)
                    try:
                        tweet = await x2.fetch_tweet(url)
                    except Exception as e2:
                        raise ParseError(f"Twitter 账号无权限或已被封禁\n\n使用的 Cookie: {self.cookie}") from e2
                else:
                    raise ParseError(str(e)) from e
            else:
                raise ParseError(str(e)) from e
        return tweet

    @staticmethod
    def _quote_block(source: TwitterTweet) -> str:
        """把一条被回复/被引用的推文渲染成引用块 (排版统一由公共 helper 决定)."""
        return format_quote_block(
            source.full_text or "",
            format_author_link(
                source.author_name or "",
                source.author_handle or "",
                profile_url(Platform.TWITTER, source.author_handle or ""),
            ),
        )

    @staticmethod
    def _build_quote(tweet: TwitterTweet) -> str:
        """把被回复的推文渲染成 Markdown 引用块, 不是回复或内容为空时返回空串."""
        return TwitterParser._quote_block(tweet.reply_to) if tweet.reply_to else ""

    @staticmethod
    def _build_quoted_block(tweet: TwitterTweet) -> str:
        """把被引用的推文渲染成 Markdown 引用块, 不是引用推文或内容为空时返回空串."""
        return TwitterParser._quote_block(tweet.quoted_status) if tweet.quoted_status else ""

    @staticmethod
    def _compose(body: str, tweet: TwitterTweet) -> str:
        """组装正文: 被回复推文在最前, 被引用推文在最后 (与 X 上的卡片位置一致)."""
        text = f"{TwitterParser._build_quote(tweet)}{body}"
        quoted = TwitterParser._build_quoted_block(tweet).strip()
        return f"{text}\n\n{quoted}" if quoted else text

    @staticmethod
    def to_media_refs(media_items: Sequence[TwitterPhoto | TwitterVideo | TwitterAni] | None) -> list[AnyMediaRef]:
        """把 provider 的媒体对象转成下载用的 ref (主推与被引用推文共用)。"""
        refs: list[AnyMediaRef] = []
        for m in media_items or []:
            match m:
                case TwitterPhoto():
                    refs.append(ImageRef(url=m.url, height=m.height, width=m.width, thumb_url=m.thumb_url))
                case TwitterVideo():
                    refs.append(
                        VideoRef(
                            url=m.url,
                            height=m.height,
                            width=m.width,
                            duration=int(m.duration_millis / 1000),
                            thumb_url=m.thumb_url,
                        )
                    )
                case TwitterAni():
                    refs.append(AniRef(url=m.url, ext="mp4", height=m.height, width=m.width, thumb_url=m.thumb_url))
        return refs

    @staticmethod
    async def media_parse(tweet: TwitterTweet) -> MultimediaParseResult | RichTextParseResult:
        media: list[AnyMediaRef] = TwitterParser.to_media_refs(tweet.media)
        # 被回复/被引用内容的媒体追加在正文媒体之后, 并用两个计数告诉渲染层怎么切:
        # 顺序是 [正文..., 被回复..., 被引用...], 让它们分别落到对应的引用块里
        reply_media = TwitterParser.to_media_refs(tweet.reply_to.media if tweet.reply_to else None)
        quoted_media = TwitterParser.to_media_refs(tweet.quoted_status.media if tweet.quoted_status else None)
        media.extend(reply_media)
        media.extend(quoted_media)
        if article := tweet.article:
            return RichTextParseResult(
                markdown_content=TwitterParser._compose(article.content, tweet),
                title=article.title,
                media=media,
                author_name=tweet.author_name,
                author_handle=tweet.author_handle,
                author_url=profile_url(Platform.TWITTER, tweet.author_handle),
                is_sensitive=tweet.is_sensitive,
                published_at=tweet.published_at,
                view_count=tweet.view_count,
                like_count=tweet.like_count,
                quoted_media_count=len(quoted_media),
                reply_media_count=len(reply_media),
            )
        return MultimediaParseResult(
            content=TwitterParser._compose(tweet.full_text, tweet),
            media=media,
            author_name=tweet.author_name,
            author_handle=tweet.author_handle,
            author_url=profile_url(Platform.TWITTER, tweet.author_handle),
            is_sensitive=tweet.is_sensitive,
            published_at=tweet.published_at,
            view_count=tweet.view_count,
            like_count=tweet.like_count,
            quoted_media_count=len(quoted_media),
            reply_media_count=len(reply_media),
        )


__all__ = ["TwitterParser"]
