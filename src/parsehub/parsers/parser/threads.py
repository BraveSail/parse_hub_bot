from ...provider_api.threads import ThreadsAPI, ThreadsAPIError, ThreadsMedia, ThreadsMediaType, ThreadsPost
from ...types import AnyMediaRef, ImageRef, MultimediaParseResult, ParseError, Platform, VideoRef
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
        media: list[AnyMediaRef] = []
        if post.media:
            pm: list[ThreadsMedia] = post.media if isinstance(post.media, list) else [post.media]
            for m in pm:
                match m.type:
                    case ThreadsMediaType.VIDEO:
                        media.append(VideoRef(url=m.url, thumb_url=m.thumb_url, width=m.width, height=m.height))
                    case ThreadsMediaType.IMAGE:
                        media.append(ImageRef(url=m.url, thumb_url=m.url, width=m.width, height=m.height))
        quote = ThreadsParser._build_quote(post)
        return MultimediaParseResult(
            content=f"{quote}{post.content}",
            media=media,
            author_name=post.author_name,
            author_handle=post.author_handle,
            published_at=post.published_at,
            view_count=post.view_count,
        )

    @staticmethod
    def _build_quote(post: ThreadsPost) -> str:
        """把被回复的帖子渲染成 Markdown 引用块, 不是回复或内容为空时返回空串."""
        reply = post.reply_to
        if not reply:
            return ""
        text = (reply.content or "").strip()
        if not text:
            return ""
        handle = (reply.author_handle or "").strip()
        name = (reply.author_name or "").strip()
        if handle:
            head = f"> 回复 @{handle}："
        elif name:
            head = f"> 回复 {name}："
        else:
            head = "> 回复："
        lines = "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())
        return f"{head}\n{lines}\n\n"

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
