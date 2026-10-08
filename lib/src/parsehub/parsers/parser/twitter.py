import html
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
from ...utils.helpers import format_author_link, format_quote_block, image_ext_from_url, profile_url
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
        """把一条被回复/被引用的推文渲染成引用块 (排版统一由公共 helper 决定).

        ⚠️ ``sign_only=bool(source.media)``: **正文为空但有媒体的推文**（很常见 ——
        推文只有一个媒体短链, 还原后正文是空串）必须照样产出引用块。不产的话
        ``_build_quoted_block`` 返回空 ⇒ 渲染层不知道那几张图属于引用卡片 ⇒
        它们被当成**正文的图**, 与主帖自己的图混进同一个图集
        （用户报「正文图为什么和引用图塞一起」）。

        只有署名行没有正文的引用块是合法的 —— 图由 ``quoted_media_count`` 通道
        放进块内。既没文字也没媒体时仍返回空 (一块孤零零的署名没有意义)。
        """
        # 投票跟着**它所属的那条推文**的正文走 —— 被引用/被回复的推文带投票时,
        # 表格渲染在它自己的引用块里（与 X 上一致）。
        body = source.full_text or ""
        if poll := TwitterParser._build_poll(source):
            body = f"{body}\n\n{poll}"
        return format_quote_block(
            body,
            format_author_link(
                source.author_name or "",
                source.author_handle or "",
                profile_url(Platform.TWITTER, source.author_handle or ""),
            ),
            sign_only=bool(source.media),
        )

    @staticmethod
    def _poll_cell(text: str) -> str:
        """表格单元格转义 —— 选项文案里的 ``|`` 会把列切断。"""
        return text.replace("|", "\\|")

    @staticmethod
    def _build_poll(tweet: TwitterTweet) -> str:
        """投票 -> markdown 表格, 没有投票时返回空串。

        形态与 linux.do 的投票一致（选项 / 票数 / 占比三列）—— 表格由渲染层
        转成服务端的 Table 块, 所以这里用 markdown 表格语法即可。

        占比按**总票数**算（不是"最高票"）; 一票都还没有时占比记 0%, 不抛错。

        选项文案里的 ``|`` 要转义 —— 否则会把表格的列切断。
        """
        poll = tweet.poll
        if not poll or not poll.choices:
            return ""
        total = sum(count for _, count in poll.choices)
        rows = [
            "| 选项 | 票数 | 占比 |",
            "| --- | --- | --- |",
        ]
        rows.extend(
            f"| {TwitterParser._poll_cell(label)} | {count} | {round(count * 100 / total) if total else 0}% |"
            for label, count in poll.choices
        )
        return "\n".join(rows)

    @staticmethod
    def _build_quote(tweet: TwitterTweet) -> str:
        """把被回复的推文渲染成 Markdown 引用块, 不是回复或内容为空时返回空串."""
        return TwitterParser._quote_block(tweet.reply_to) if tweet.reply_to else ""

    @staticmethod
    def _build_quoted_block(tweet: TwitterTweet) -> str:
        """把被引用的推文渲染成 Markdown 引用块, 不是引用推文或内容为空时返回空串."""
        return TwitterParser._quote_block(tweet.quoted_status) if tweet.quoted_status else ""

    @staticmethod
    def _quote_roles(tweet: TwitterTweet) -> list[str]:
        """正文里引用块的**角色**, 按出现顺序 (``reply`` 在前、``quoted`` 在后)。

        判据与 ``_compose`` **同源**（都看 ``_build_quote`` / ``_build_quoted_block``
        是否非空）—— 两处判据分家的话, roles 会说"有块"而正文里没有, 媒体就配不到。
        """
        roles: list[str] = []
        if TwitterParser._build_quote(tweet):
            roles.append("reply")
        if TwitterParser._build_quoted_block(tweet):
            roles.append("quoted")
        return roles

    @staticmethod
    def _compose(body: str, tweet: TwitterTweet, *, reply_yt: str = "", quoted_yt: str = "") -> str:
        """组装正文: 被回复推文在最前, 被引用推文在最后 (与 X 上的卡片位置一致).

        :param reply_yt: 被回复推文正文里的 YouTube 卡片, 追加进**回复**引用块
        :param quoted_yt: 被引用推文正文里的 YouTube 卡片, 追加进**被引用**引用块
        """
        if poll := TwitterParser._build_poll(tweet):
            body = f"{body}\n\n{poll}"
        reply_block = TwitterParser._append_inside_quote(TwitterParser._build_quote(tweet), reply_yt)
        text = f"{reply_block}{body}"
        # strip: 引用块自带末尾空行（``format_quote_block`` 的块结束约定），
        # 拼接由这里负责，别让它在正文中段多出一个空行。
        quoted_block = TwitterParser._append_inside_quote(
            TwitterParser._build_quoted_block(tweet), quoted_yt
        ).strip()
        return f"{text}\n\n{quoted_block}" if quoted_block else text

    @staticmethod
    def _append_inside_quote(block: str, extra: str) -> str:
        """把内容追加到引用块**内部**。

        ``format_quote_block`` 末尾自带一个空行（块靠它结束），所以必须插在那个空行
        **之前** —— 插在之后就成了独立段落, 媒体会和卡片分家。
        """
        if not block or not extra:
            return block
        return f"{block.rstrip()}\n{extra}\n\n"

    @staticmethod
    async def _youtube_card(text: str | None) -> tuple[str, list[AnyMediaRef]]:
        """把正文里的 YouTube 链接渲染成引用卡片 (标题可点) + 封面图。

        **作用域: 只给被引用 / 被回复的推文用** —— 它们本来就渲染成引用卡片,
        卡片正文里的链接应当有封面 (与 bilibili 引用的形态一致, 用户要求)。

        ⚠️ **主帖自己的正文不处理**: 它既不是引用也不是回复, 链接原样留着
        (用户原话:「有引用就引用吗, 没引用就不弄, 链接你放那里不管就行了」)。

        只取**第一个**能抓到卡片的链接: 一条推文塞多张封面会喧宾夺主, 而且卡片之间
        没有各自的位置信息 (引用块媒体是按数量切分的, 见 ``quoted_media_count``)。

        抓不到 (网络失败/链接不是频道也不是视频) 就返回空 —— 封面是锦上添花,
        不该让整条解析失败。
        """
        from ...provider_api.youtube import fetch_card, find_youtube_links

        for link in find_youtube_links(text):
            card = await fetch_card(link)
            if card:
                href = html.escape(card.url, quote=True)
                label = html.escape(card.title)
                return f'> <i><a href="{href}">{label}</a></i>', [ImageRef(url=card.cover_url)]
        return "", []

    @staticmethod
    def _hashtags(tweet: TwitterTweet) -> list[str]:
        """正文与被引用推文的标签，去重保序。

        被引用推文的标签也要 —— 它渲染成引用卡片，卡片正文里的标签同样要能点
        （渲染层对引用块也走同一套链接化）。
        """
        out = list(tweet.hashtags)
        seen = set(out)
        for other in (tweet.quoted_status, tweet.reply_to):
            for tag in getattr(other, "hashtags", None) or []:
                if tag not in seen:
                    seen.add(tag)
                    out.append(tag)
        return out

    @staticmethod
    def to_media_refs(media_items: Sequence[TwitterPhoto | TwitterVideo | TwitterAni] | None) -> list[AnyMediaRef]:
        """把 provider 的媒体对象转成下载用的 ref (主推与被引用推文共用)。"""
        refs: list[AnyMediaRef] = []
        for m in media_items or []:
            match m:
                case TwitterPhoto():
                    refs.append(
                        ImageRef(
                            url=m.url,
                            ext=image_ext_from_url(m.url),
                            height=m.height,
                            width=m.width,
                            thumb_url=m.thumb_url,
                        )
                    )
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

        # 被**引用 / 被回复**推文正文里的 YouTube 链接 -> 引用卡片 (标题可点) + 封面。
        # 只有这两种推文才处理: 它们本来就渲染成引用卡片, 卡片里的链接该有封面。
        # **主帖自己的正文不处理** —— 它既不是引用也不是回复, 链接原样留着。
        reply_yt_quote, reply_yt_media = (
            await TwitterParser._youtube_card(tweet.reply_to.full_text) if tweet.reply_to else ("", [])
        )
        quoted_yt_quote, quoted_yt_media = (
            await TwitterParser._youtube_card(tweet.quoted_status.full_text) if tweet.quoted_status else ("", [])
        )

        # 媒体顺序 = [正文..., 被回复..., 被引用...]（渲染层从末尾往前切两个引用块）。
        # 每档里"卡片自己正文的 YouTube 封面"排在该档媒体的**末尾**。
        reply_media = [
            *TwitterParser.to_media_refs(tweet.reply_to.media if tweet.reply_to else None),
            *reply_yt_media,
        ]
        quoted_media = [
            *TwitterParser.to_media_refs(tweet.quoted_status.media if tweet.quoted_status else None),
            *quoted_yt_media,
        ]
        media.extend(reply_media)
        media.extend(quoted_media)

        # 计数 = 各档**真会渲染出引用块**时的媒体数。判据是数据层有没有那条推文
        # （不是"正文里有没有 `> ` 形态的文字"）。
        quoted_total = len(quoted_media) if (tweet.quoted_status and TwitterParser._build_quoted_block(tweet)) else 0
        reply_total = (
            len(reply_media)
            if (tweet.reply_to and (TwitterParser._build_quote(tweet) or reply_yt_quote))
            else 0
        )
        if article := tweet.article:
            return RichTextParseResult(
                markdown_content=TwitterParser._compose(
                    article.content, tweet, reply_yt=reply_yt_quote, quoted_yt=quoted_yt_quote
                ),
                title=article.title,
                media=media,
                author_name=tweet.author_name,
                author_handle=tweet.author_handle,
                author_url=profile_url(Platform.TWITTER, tweet.author_handle),
                is_sensitive=tweet.is_sensitive,
                published_at=tweet.published_at,
                view_count=tweet.view_count,
                like_count=tweet.like_count,
                quoted_media_count=quoted_total,
                reply_media_count=reply_total,
                # 引用块的角色（按出现顺序）—— 渲染层据此归位媒体, 不再看位置
                quote_roles=TwitterParser._quote_roles(tweet),
                # 标签走**平台实体**（服务端算好的边界），渲染层据此精确链接化
                hashtags=TwitterParser._hashtags(tweet),
            )
        return MultimediaParseResult(
            content=TwitterParser._compose(
                tweet.full_text, tweet, reply_yt=reply_yt_quote, quoted_yt=quoted_yt_quote
            ),
            media=media,
            author_name=tweet.author_name,
            author_handle=tweet.author_handle,
            author_url=profile_url(Platform.TWITTER, tweet.author_handle),
            is_sensitive=tweet.is_sensitive,
            published_at=tweet.published_at,
            view_count=tweet.view_count,
            like_count=tweet.like_count,
            quoted_media_count=quoted_total,
            reply_media_count=reply_total,
            quote_roles=TwitterParser._quote_roles(tweet),
            hashtags=TwitterParser._hashtags(tweet),
        )


__all__ = ["TwitterParser"]
