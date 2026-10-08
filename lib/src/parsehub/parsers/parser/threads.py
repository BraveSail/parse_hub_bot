from ...provider_api.threads import ThreadsAPI, ThreadsAPIError, ThreadsMedia, ThreadsMediaType, ThreadsPost
from ...types import AnyMediaRef, ImageRef, MultimediaParseResult, ParseError, Platform, VideoRef
from ...utils.helpers import format_author_link, format_quote_block, image_ext_from_url, profile_url
from ..base.base import BaseParser


class ThreadsParser(BaseParser):
    __platform__ = Platform.THREADS
    __supported_type__ = ["视频", "图文"]
    __match__ = r"^(http(s)?://)?.+threads.com/(@[\w.]+/post|share)/.*"
    __redirect_keywords__ = ["/share/"]

    async def get_raw_url(self, url: str, *, clean_all: bool = False, headers: dict | None = None) -> str:
        return await super().get_raw_url(url, clean_all=clean_all, headers={})

    async def _do_parse(self, raw_url: str) -> "MultimediaParseResult":
        post = await self._parse(raw_url)
        media = ThreadsParser._to_refs(post.media)
        # 被回复帖的媒体也要发 —— 它渲染成回复卡片, 卡片里的图不能丢
        # (用户报「回复的图片丢了」)。顺序与 twitter 一致: 正文媒体在前, 回复媒体在后,
        # 再用 ``reply_media_count`` 告诉渲染层"末尾这几个属于回复卡片"。
        reply_media = ThreadsParser._to_refs(post.reply_to.media if post.reply_to else None)
        media.extend(reply_media)
        quote = ThreadsParser._build_quote(post)
        return MultimediaParseResult(
            content=f"{quote}{post.content}",
            media=media,
            author_name=post.author_name,
            author_handle=post.author_handle,
            author_url=profile_url(Platform.THREADS, post.author_handle),
            published_at=post.published_at,
            view_count=post.view_count,
            like_count=post.like_count,
            reply_media_count=len(reply_media),
            # 引用块的角色（被回复帖在最前）—— 渲染层据此归位媒体, 不再靠位置
            quote_roles=["reply"] if quote else [],
        )

    @staticmethod
    def _to_refs(raw: "ThreadsMedia | list[ThreadsMedia] | None") -> list[AnyMediaRef]:
        """把 provider 的媒体对象转成结果用的 ref（单条与列表两种形态都吃）。"""
        if not raw:
            return []
        items: list[ThreadsMedia] = raw if isinstance(raw, list) else [raw]
        out: list[AnyMediaRef] = []
        for m in items:
            if m.type == ThreadsMediaType.VIDEO:
                out.append(VideoRef(url=m.url, thumb_url=m.thumb_url, width=m.width, height=m.height))
            elif m.type == ThreadsMediaType.IMAGE:
                out.append(
                    ImageRef(
                        url=m.url,
                        thumb_url=m.url,
                        width=m.width,
                        height=m.height,
                        ext=image_ext_from_url(m.url),
                    )
                )
        return out

    @staticmethod
    def _build_quote(post: ThreadsPost) -> str:
        """把被回复的帖子渲染成引用块 (排版统一由公共 helper 决定)."""
        reply = post.reply_to
        if not reply:
            return ""
        return format_quote_block(
            reply.content or "",
            format_author_link(
                reply.author_name or "",
                reply.author_handle or "",
                profile_url(Platform.THREADS, reply.author_handle or ""),
            ),
        )

    async def _parse(self, url: str) -> ThreadsPost:
        # 公开帖子无需登录即可解析; 登录墙内容 (私密/受限/年龄限制) 才需要 Cookie, 有则带上
        try:
            api = ThreadsAPI(proxy=self.proxy, cookie=self.cookie.get_value() if self.cookie else None)
            return await api.parse(url)
        except ThreadsAPIError as e:
            if not self.cookie:
                raise ParseError("无法获取帖子内容: 该帖子可能位于登录墙内, 请为 threads 平台配置 Cookie") from e
            raise ParseError("无法获取帖子内容(可能为私人或受限内容, 或 Cookie 已失效)") from e
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"无法获取帖子内容: {e}") from e


__all__ = ["ThreadsParser"]
