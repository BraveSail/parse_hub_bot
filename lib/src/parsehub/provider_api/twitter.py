# mypy: disable-error-code=no-untyped-def
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NamedTuple

from loguru import logger

from ..types import ParseError
from ..utils import http
from ..utils.helpers import UA, to_datetime, to_int


class Twitter:
    def __init__(self, proxy: str | None = None, cookie: dict | None = None):
        self.proxy = proxy
        self.authorization = (
            "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOu"
            "H5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
        )
        self.cookie = cookie

    async def fetch_tweet(self, url: str) -> TwitterTweet:
        tweet = self.parse(await self._fetch_result(self.get_id_by_url(url)))
        if tweet.reply_to_id:
            tweet.reply_to = await self._fetch_related(tweet.reply_to_id, "被回复推文")
        if tweet.quoted_status_id and tweet.quoted_status is None:
            tweet.quoted_status = await self._fetch_related(tweet.quoted_status_id, "被引用推文")
        return tweet

    async def _fetch_related(self, tweet_id: str, label: str) -> TwitterTweet | None:
        """补取一条关联推文 (被回复/被引用), 失败只跳过, 不阻断主解析."""
        try:
            return self.parse(await self._fetch_result(tweet_id))
        except Exception as e:
            logger.warning(f"获取{label}失败, 跳过引用: {e}")
            return None

    async def _fetch_result(self, tweet_id: str) -> dict:
        headers = {
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": self.authorization,
            "content-type": "application/json",
            "user-agent": UA,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "zh-cn",
        }

        cookie = None
        if self.cookie and self.check_cookie():
            headers["x-csrf-token"] = self.cookie.get("ct0", "")
            cookie = self.cookie

        params = {
            "variables": f'{{"tweetId":"{tweet_id}","withComm'
            f'unity":false,"includePromotedContent":false,"withVoice":false}}',
            "features": '{"creator_subscriptions_tweet_preview_api_enabled":true,'
            '"communities_web_enable_tweet_community_results_fetch":true,'
            '"c9s_tweet_anatomy_moderator_badge_enabled":true,"tweetypie_unmention_optimization_enabled":true,'
            '"responsive_web_edit_tweet_api_enabled":true,"graphql_is_translatable_rweb_tweet_is_translatable_enabled"'
            ":true,"
            '"view_counts_everywhere_api_enabled":true,"longform_notetweets_consumption_enabled":true,'
            '"responsive_web_twitter_article_tweet_consumption_enabled":true,"tweet_awards_web_tipping_enabled":false,'
            '"creator_subscriptions_quote_tweet_preview_enabled":false,"freedom_of_speech_not_reach_fetch_enabled"'
            ":true,"
            '"standardized_nudges_misinfo":true,"tweet_with_visibility_results_prefer_gql_limited_actions_policy_enable'
            'd":true,'
            '"tweet_with_visibility_results_prefer_gql_media_interstitial_enabled":false,"rweb_video_timestamps_enabled'
            '":true,'
            '"longform_notetweets_rich_text_read_enabled":true,"longform_notetweets_inline_media_enabled":true,'
            '"rweb_tipjar_consumption_enabled":true,"responsive_web_graphql_exclude_directive_enabled":true,"verified_'
            'phone_label_enabled":false,'
            '"responsive_web_graphql_skip_user_profile_image_extensions_enabled":false,"responsive_web_graphql_timeline'
            '_navigation_enabled":true,'
            '"responsive_web_enhance_cards_enabled":false}',
            "fieldToggles": '{"withArticleRichContentState":true,"withArticlePlainText":false}',
        }

        async with http.AsyncClient(proxy=self.proxy) as client:
            response = await client.get(
                "https://api.twitter.com/graphql/kPLTRmMnzbPTv70___D06w/TweetResultByRestId",
                params=params,
                headers=headers,
                cookies=cookie,
            )
        response.raise_for_status()
        return response.json()

    def parse(self, result: dict) -> TwitterTweet:
        if e := result.get("errors"):
            raise Exception(f"error -1: {e[0]['message']}")

        result = result["data"]["tweetResult"].get("result")
        if not result:
            raise ParseError("error -4: 帖子或用户不存在")

        return self._parse_result(result)

    def _parse_result(self, result: dict) -> TwitterTweet:
        """解析 tweetResult.result 结构 (顶层与内嵌的被引用推文同构)."""
        # 响应有两种形态: `{tweet: {...}}` (包装) 与字段直接铺在 result 上 (平铺)。
        # 字段一律从 node 取 —— 固定取顶层 result 时, 包装形态下长文正文 (note_tweet)、
        # 作者、浏览量、article 会**全部悄悄丢失** (字段根本不在那一层)。
        if tweet := result.get("tweet"):
            node = tweet
            tweet_id = tweet.get("rest_id", {})
            legacy: dict | None = tweet.get("legacy")
        else:
            node = result
            tweet_id = result.get("rest_id", {})
            legacy = result.get("legacy")

        if not legacy:
            if result.get("__typename") == "TweetTombstone":
                raise Exception("error -2: 该推文开启了限制, 匿名用户无法查看")
            raise Exception(f"error -3: {result.get('reason')}")

        author_name = self._extract_author_name(node)
        author_handle = self._extract_author_handle(node)
        reply_to_id = str(legacy.get("in_reply_to_status_id_str") or "")
        quoted_status_id = str(legacy.get("quoted_status_id_str") or "")
        quoted_status = self._parse_quoted(node)
        # 发布时间在 legacy.created_at, 浏览量在 views.count (字符串)
        published_at = legacy.get("created_at")
        view_count = (node.get("views") or {}).get("count")
        # 点赞数在 legacy.favorite_count (匿名请求同样返回)
        like_count = legacy.get("favorite_count")

        if article := node.get("article", {}):
            ta = ArticleRenderer(article["article_results"]["result"]).render()
            return TwitterTweet(
                tweet_id=tweet_id,
                article=ta,
                author_name=author_name,
                author_handle=author_handle,
                reply_to_id=reply_to_id,
                quoted_status_id=quoted_status_id,
                quoted_status=quoted_status,
                published_at=published_at,
                view_count=view_count,
                like_count=like_count,
                is_sensitive=bool(legacy.get("possibly_sensitive")),
            )

        if note_tweet := node.get("note_tweet"):
            note_result = note_tweet.get("note_tweet_results", {}).get("result", {})
            full_text = note_result.get("text", None)
            url_entities = (note_result.get("entity_set") or {}).get("urls") or []
            if not full_text:
                full_text = legacy.get("full_text", "")
                url_entities = legacy["entities"].get("urls", [])
        else:
            full_text = legacy.get("full_text", "")
            url_entities = legacy["entities"].get("urls", [])

        full_text = self._restore_short_urls(full_text, url_entities)

        media = legacy["entities"].get("media", [])
        media_list: list[TwitterVideo | TwitterPhoto | TwitterAni] = []
        for i in media:
            original_info = i.get("original_info", {})
            height = original_info.get("height", 0)
            width = original_info.get("width", 0)
            media_url_https = i["media_url_https"]

            match i["type"]:
                case "photo":
                    media_list.append(
                        TwitterPhoto(
                            url=self._build_img_url(media_url_https, "orig"),
                            width=width,
                            height=height,
                            thumb_url=self._build_img_url(media_url_https, "small"),
                        )
                    )
                case "video":
                    video_info = i.get("video_info", {})
                    media_list.append(
                        TwitterVideo(
                            url=video_info["variants"][-1]["url"],
                            height=height,
                            width=width,
                            duration_millis=video_info.get("duration_millis", 0),
                            thumb_url=self._build_img_url(media_url_https, "medium"),
                        )
                    )
                case "animated_gif":
                    media_list.append(
                        TwitterAni(
                            url=i["video_info"]["variants"][-1]["url"],
                            height=height,
                            width=width,
                            thumb_url=self._build_img_url(media_url_https, "small"),
                        )
                    )

        if card_photo := self._parse_card_photo(node):
            # 外链卡片的预览图排在推文自带媒体**之后** (与 X 上的显示顺序一致)
            media_list.append(card_photo)

        return TwitterTweet(
            tweet_id=tweet_id,
            full_text=full_text,
            media=media_list or None,
            author_name=author_name,
            author_handle=author_handle,
            reply_to_id=reply_to_id,
            quoted_status_id=quoted_status_id,
            quoted_status=quoted_status,
            published_at=published_at,
            view_count=view_count,
            like_count=like_count,
            is_sensitive=bool(legacy.get("possibly_sensitive")),
        )

    @staticmethod
    def _parse_card_photo(node: dict) -> TwitterPhoto | None:
        """外链卡片的预览图 —— 推文贴了链接时, X 会给那个网页生成一张卡片图。

        图由 X 托管在 ``pbs.twimg.com/card_img/...``, 字段在 ``node["card"]`` 里,
        **不在** ``legacy.entities.media`` 那条路上 —— 所以以前整条丢掉:
        正文里只有个链接的推文, 图片一张都发不出来 (用户报「里面有图, 没抓到」)。

        播放器类卡片 (YouTube 等) 跳过: 那条路有专门处理 (parser 层 ``_youtube_card``
        走 oembed 拿真封面), 这里再补一张就成了两张封面。
        """
        card = ((node.get("card") or {}).get("legacy")) or {}
        if "player" in (card.get("name") or ""):
            return None

        values = {b.get("key"): (b.get("value") or {}) for b in (card.get("binding_values") or [])}
        # 同一张图有十几个尺寸变体, 按"最大优先"取 name=orig 的那些
        for key in (
            "photo_image_full_size_original",
            "summary_photo_image_original",
            "thumbnail_image_original",
            "photo_image_full_size_large",
            "summary_photo_image_large",
        ):
            image = (values.get(key) or {}).get("image_value") or {}
            url = image.get("url")
            if not url:
                continue
            thumb = ((values.get("thumbnail_image") or {}).get("image_value") or {}).get("url")
            return TwitterPhoto(
                url=url,
                height=image.get("height", 0) or 0,
                width=image.get("width", 0) or 0,
                thumb_url=thumb,
            )
        return None

    def _parse_quoted(self, result: dict) -> TwitterTweet | None:
        """解析内嵌的被引用推文.

        被引用推文的数据通常就在同一份响应里 (quoted_status_result), 不需要额外请求;
        但 X 会把它降级成 TweetUnavailable (实测匿名请求下稳定如此), 也可能已被删除或受限,
        因此失败一律只跳过 —— 缺失时由 fetch_tweet 按 quoted_status_id 补取一次.
        """
        quoted = (result.get("quoted_status_result") or {}).get("result")
        if not quoted:
            return None
        try:
            return self._parse_result(quoted)
        except Exception as e:
            logger.debug(f"内嵌的被引用推文不可用, 稍后按 ID 补取: {e}")
            return None

    @staticmethod
    def _restore_short_urls(text: str, url_entities: list[dict]) -> str:
        """用 entities 中的 expanded_url 还原正文里的 t.co 短链。"""
        for entity in url_entities:
            short_url = entity.get("url")
            expanded_url = entity.get("expanded_url")
            if short_url and expanded_url:
                text = text.replace(short_url, expanded_url)
        return text

    @staticmethod
    def _extract_author_name(result: dict) -> str:
        user_result = result.get("core", {}).get("user_results", {}).get("result", {})
        legacy = user_result.get("legacy", {}) if isinstance(user_result, dict) else {}
        return str(legacy.get("name") or legacy.get("screen_name") or "").strip()

    @staticmethod
    def _extract_author_handle(result: dict) -> str:
        user_result = result.get("core", {}).get("user_results", {}).get("result", {})
        legacy = user_result.get("legacy", {}) if isinstance(user_result, dict) else {}
        return str(legacy.get("screen_name") or "").strip()

    @staticmethod
    def _build_img_url(url: str, size: Literal["orig", "large", "medium", "small", "thumb"]):
        p = "&" if "?" in url else "?"
        return f"{url}{p}name={size}"

    @staticmethod
    def get_id_by_url(url: str) -> str:
        match = re.search(r"status/(\d+)", url)
        if not match:
            raise ValueError(f"Invalid tweet url: {url}")
        return match[1]

    def check_cookie(self) -> bool:
        if not self.cookie:
            return False
        if not self.cookie.get("ct0"):
            logger.warning("cookie 缺少必要参数: ct0")
            return False
        if not self.cookie.get("auth_token"):
            logger.warning("cookie 缺少必要参数: auth_token")
            return False
        return True


class TwitterTweet:
    def __init__(
        self,
        tweet_id: str,
        full_text: str = "",
        media: list[TwitterVideo | TwitterPhoto | TwitterAni] | None = None,
        article: TwitterArticle | None = None,
        author_name: str = "",
        author_handle: str = "",
        reply_to_id: str = "",
        reply_to: TwitterTweet | None = None,
        quoted_status_id: str = "",
        quoted_status: TwitterTweet | None = None,
        is_sensitive: bool = False,
        published_at: datetime | None = None,
        view_count: int | None = None,
        like_count: int | None = None,
    ):
        self.tweet_id = tweet_id
        self.full_text = re.sub(r"\s*https://t\.co/[^\s,]+$", "", full_text or "") if media else full_text
        self.media = media
        self.article = article
        self.author_name = author_name
        self.author_handle = author_handle
        self.reply_to_id = reply_to_id
        """被回复推文的 ID，空字符串表示不是回复"""
        self.reply_to: TwitterTweet | None = reply_to
        """被回复的推文（由 fetch_tweet 填充，仅在是回复时）"""
        self.quoted_status_id = quoted_status_id
        """被引用推文的 ID，空字符串表示不是引用推文"""
        self.quoted_status: TwitterTweet | None = quoted_status
        """被引用的推文（优先取响应内嵌数据，缺失时由 fetch_tweet 按 ID 补取）"""
        self.is_sensitive = is_sensitive
        """推文是否被标记为敏感内容 (legacy.possibly_sensitive)"""
        self.published_at = to_datetime(published_at)
        """发布时间 (legacy.created_at)"""
        self.view_count = to_int(view_count)
        """浏览量 (views.count)"""
        self.like_count = to_int(like_count)
        """点赞数 (legacy.favorite_count)"""


@dataclass
class TwitterArticle:
    title: str
    content: str
    media: list[TwitterVideo | TwitterPhoto] | None = None


@dataclass
class TwitterVideo:
    url: str
    height: int
    width: int
    duration_millis: int
    thumb_url: str | None = None


@dataclass
class TwitterPhoto:
    url: str
    height: int
    width: int
    thumb_url: str | None = None


@dataclass
class TwitterAni:
    url: str
    height: int
    width: int
    thumb_url: str | None = None


class _Insertion(NamedTuple):
    """待插入原文的 Markdown 标记。"""

    idx: int
    text: str
    kind: str  # "start" | "end" | "atomic"
    length: int = 0


class ArticleRenderer:
    """将 Twitter Article JSON 解析并渲染为 Markdown。"""

    # 行内样式 → Markdown 标记
    _INLINE_STYLES: dict[str, str] = {
        "Bold": "**",
        "Italic": "*",
        "Strikethrough": "~~",
    }

    # 块级类型 → 格式化函数
    _BLOCK_FORMATTERS: dict[str, Callable[[str], str]] = {
        "header-one": lambda t: f"# {t}",
        "header-two": lambda t: f"## {t}",
        "header-three": lambda t: f"### {t}",
        "blockquote": lambda t: "\n".join(f"> {line}" for line in t.split("\n")),
        "ordered-list-item": lambda t: f"1. {t}",
        "unordered-list-item": lambda t: f"- {t}",
    }

    def __init__(self, article_data: dict):
        self._data = article_data
        self._media_dict: dict = {}
        self._media_result: list[TwitterPhoto | TwitterVideo] = []

    # ── 公共入口 ──────────────────────────────

    def render(self) -> TwitterArticle:
        content_state = self._data.get("content_state", {})
        blocks = content_state.get("blocks", [])
        entity_map = {str(item["key"]): item["value"] for item in content_state.get("entityMap", [])}
        title = self._data.get("title", "")

        self._parse_media_entities()
        cover_url = self._data.get("cover_media", {}).get("media_info", {}).get("original_img_url", "")

        md_lines: list[str] = []
        if cover_url:
            md_lines.append(f"![Cover Image]({cover_url})\n")

        for block in blocks:
            md_lines.append(self._render_block(block, entity_map))

        return TwitterArticle(
            title=title,
            content="\n\n".join(md_lines),
            media=self._media_result or None,
        )

    # ── 媒体解析 ──────────────────────────────

    def _parse_media_entities(self) -> None:
        for media in self._data.get("media_entities", []):
            media_id = media.get("media_id")
            media_info = media.get("media_info", {})
            typename = media_info.get("__typename")

            if typename == "ApiImage":
                self._parse_image(media_id, media_info)
            elif typename == "ApiVideo":
                self._parse_video(media_id, media_info)

    def _parse_image(self, media_id, info: dict) -> None:
        url = info.get("original_img_url", "")
        if media_id and url:
            self._media_dict[media_id] = {"type": "image", "url": url}
        self._media_result.append(
            TwitterPhoto(
                url=url,
                height=info.get("original_img_height", 0),
                width=info.get("original_img_width", 0),
            )
        )

    def _parse_video(self, media_id, info: dict) -> None:
        preview = info.get("preview_image", {})
        preview_url = preview.get("original_img_url", "")
        video_url = self._best_mp4_url(info.get("variants", []))

        if media_id and preview_url:
            self._media_dict[media_id] = {
                "type": "video",
                "preview_url": preview_url,
                "video_url": video_url,
            }
        self._media_result.append(
            TwitterVideo(
                url=video_url,
                height=preview.get("original_img_height", 0),
                width=preview.get("original_img_width", 0),
                duration_millis=info.get("duration_millis", 0),
                thumb_url=preview_url,
            )
        )

    @staticmethod
    def _best_mp4_url(variants: list) -> str:
        mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
        if not mp4s:
            return ""
        return str(max(mp4s, key=lambda v: v.get("bit_rate", 0)).get("url", ""))

    # ── Block 渲染 ────────────────────────────

    def _render_block(self, block: dict, entity_map: dict) -> str:
        b_type = block.get("type", "unstyled")
        text = block.get("text", "")

        insertions = self._collect_inline_styles(block)
        insertions += self._collect_entities(block, entity_map)
        insertions.sort(key=self._insertion_sort_key)

        final_text = self._apply_insertions(text, insertions)
        formatter = self._BLOCK_FORMATTERS.get(b_type)
        return formatter(final_text) if formatter else final_text

    @staticmethod
    def _collect_inline_styles(block: dict) -> list[_Insertion]:
        result: list[_Insertion] = []
        for style in block.get("inlineStyleRanges", []):
            marker = ArticleRenderer._INLINE_STYLES.get(style["style"])
            if not marker:
                continue
            offset, length = style["offset"], style["length"]
            result.append(_Insertion(offset, marker, "start", length))
            result.append(_Insertion(offset + length, marker, "end", length))
        return result

    def _collect_entities(self, block: dict, entity_map: dict) -> list[_Insertion]:
        result: list[_Insertion] = []
        for ent in block.get("entityRanges", []):
            offset, length = ent["offset"], ent["length"]
            ent_data = entity_map.get(str(ent["key"]), {})
            ent_type = ent_data.get("type")

            if ent_type == "LINK":
                url = ent_data.get("data", {}).get("url", "")
                result.append(_Insertion(offset, "[", "start", length))
                result.append(_Insertion(offset + length, f"]({url})", "end", length))

            elif ent_type == "MEDIA":
                md = self._media_entity_to_md(ent_data)
                if md:
                    result.append(_Insertion(offset, md, "atomic", length))

            elif ent_type == "DIVIDER":
                result.append(_Insertion(offset, "\n---\n", "atomic", length))

        return result

    def _media_entity_to_md(self, ent_data: dict) -> str:
        media_items = ent_data.get("data", {}).get("mediaItems", [])
        if not media_items:
            return ""
        obj = self._media_dict.get(media_items[0].get("mediaId"))
        if not obj:
            return ""

        if obj["type"] == "image":
            return f"![Image]({obj['url']})"
        if obj["type"] == "video":
            p, v = obj["preview_url"], obj["video_url"]
            return f"[![Video]({p})]({v})" if v else f"![Video Preview]({p})"
        return ""

    # ── 文本拼装 ──────────────────────────────

    @staticmethod
    def _insertion_sort_key(ins: _Insertion) -> tuple:
        weight = {"end": 1, "atomic": 0, "start": -1}.get(ins.kind, 0)
        return -ins.idx, weight, ins.length

    @staticmethod
    def _apply_insertions(text: str, insertions: list[_Insertion]) -> str:
        chars = list(text)
        for ins in insertions:
            if ins.kind == "atomic" and ins.idx < len(chars):
                chars[ins.idx] = ins.text
            else:
                chars.insert(ins.idx, ins.text)
        return "".join(chars)
