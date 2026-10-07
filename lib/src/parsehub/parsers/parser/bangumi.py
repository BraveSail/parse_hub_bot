"""bgm.tv（Bangumi 番组计划）解析器：日志 + 小组话题。

两个页面**不同构**，所以是两条解析路径（选择器、正文容器、楼层结构都不一样），
但共用同一套 BBCode → markdown 转换（见 ``provider_api/bangumi.py``）。

- **日志** ``/blog/<id>``：单篇，标题在 ``.header h1.title``
- **小组话题** ``/group/topic/<id>``：主楼 + 楼层，标题在 ``h1``（``<br/>`` 之后），
  楼层号形如 ``#2`` / ``#2-1``（楼中楼）

条目页（``/subject/<id>``）与小组首页（``/group/<slug>``）都不走这里。
"""

from ...provider_api.bangumi import BangumiBlog, BangumiError, BangumiTopic
from ...types import ImageRef, ParseError, Platform, RichTextParseResult
from ...utils.helpers import profile_url
from ..base.base import BaseParser


class BangumiParseResult(RichTextParseResult):
    """bgm.tv 图文（日志 / 小组话题）。"""

    #: 图片**已从正文里抽走**（见 provider），必须下载后当附件发送 ——
    #: 不置位的话流水线会跳过下载，图片就彻底丢了（与 linux.do 同一理由）。
    requires_media_download = True


class BangumiParser(BaseParser):
    __platform__ = Platform.BANGUMI
    __supported_type__ = ["图文"]
    # 锚定 ``blog/<数字>``、``group/topic/<数字>``、``subject/topic/<数字>``：
    # bgm.tv 与 bangumi.tv 两个域名都有人用。
    # **不接** ``/subject/<id>``（条目页本身，官方 API 覆盖）与 ``/group/<slug>``（小组首页）。
    __match__ = (
        r"^(http(s)?://)?(bgm|bangumi)\.tv/"
        r"(blog/\d+"
        r"|(group|subject)/topic/\d+"
        r"|rakuen/topic/(group|subject)/\d+)"  # 「超展开」入口，与上面同话题
    )

    async def _do_parse(self, raw_url: str) -> "BangumiParseResult":
        try:
            if "/topic/" in raw_url:
                return await self._parse_topic(raw_url)
            return await self._parse_blog(raw_url)
        except BangumiError as e:
            raise ParseError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法获取内容: {e}") from e

    async def _parse_blog(self, raw_url: str) -> "BangumiParseResult":
        blog = await BangumiBlog.parse(
            raw_url,
            proxy=self.proxy,
            cookie=self.cookie.get_value() if self.cookie else None,
        )
        return BangumiParseResult(
            title=blog.title,
            markdown_content=blog.markdown_content,
            media=_refs(blog.images),
            author_name=blog.author_name,
            # bgm 没有 @用户名：标识可能是数字 uid（老用户）或 slug（新用户），
            # 主页与日志标签页都用它拼
            author_handle=blog.author_handle,
            # 模板是 ``/user/{id}`` —— 标识要按 **user_id** 传（按 handle 传会拼不出地址，
            # 静默得到空串 ⇒ 作者行不可点）
            author_url=profile_url(Platform.BANGUMI, user_id=blog.author_handle),
            published_at=blog.published_at,
            tags=blog.tags,
        )

    async def _parse_topic(self, raw_url: str) -> "BangumiParseResult":
        """小组话题 / 条目讨论版 —— **只发一层**（分享的楼层，或主楼）。

        分享楼层时主楼会作为引用块带上（见 provider 的 ``_quote_of``），
        它的图片走 ``quoted_media_count`` 那个通道进引用块内部。
        """
        topic = await BangumiTopic.parse(
            raw_url,
            proxy=self.proxy,
            cookie=self.cookie.get_value() if self.cookie else None,
        )
        return BangumiParseResult(
            title=topic.title,
            markdown_content=topic.markdown_content,
            media=_refs(topic.images),
            author_name=topic.author_name,
            author_handle=topic.author_handle,
            author_url=profile_url(Platform.BANGUMI, user_id=topic.author_handle),
            published_at=topic.published_at,
            quoted_media_count=topic.quoted_media_count,
            # 本层的楼层号（主楼是 ``#1``）—— 通用位置标记机制
            position_label=topic.floor_label,
        )


def _refs(images: list) -> list[ImageRef]:
    return [ImageRef(url=i.url, thumb_url=i.url, width=i.width, height=i.height) for i in images]


__all__ = ["BangumiParseResult", "BangumiParser"]
