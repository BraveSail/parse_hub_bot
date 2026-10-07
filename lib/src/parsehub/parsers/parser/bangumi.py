"""bgm.tv（Bangumi 番组计划）解析器：日志 + 小组话题。

两个页面**不同构**，所以是两条解析路径（选择器、正文容器、楼层结构都不一样），
但共用同一套 BBCode → markdown 转换（见 ``provider_api/bangumi.py``）。

- **日志** ``/blog/<id>``：单篇，标题在 ``.header h1.title``
- **小组话题** ``/group/topic/<id>``：主楼 + 楼层，标题在 ``h1``（``<br/>`` 之后），
  楼层号形如 ``#2`` / ``#2-1``（楼中楼）

条目页（``/subject/<id>``）与小组首页（``/group/<slug>``）都不走这里。
"""

from ...provider_api.bangumi import BangumiBlog, BangumiError, BangumiGroupTopic
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
    # 锚定 ``blog/<数字>`` 与 ``group/topic/<数字>``：bgm.tv 与 bangumi.tv 两个域名都有人用。
    # **不接** ``/subject/<id>``（官方 API 覆盖）与 ``/group/<slug>``（小组首页，不是话题）。
    __match__ = r"^(http(s)?://)?(bgm|bangumi)\.tv/(blog/\d+|group/topic/\d+)"

    async def _do_parse(self, raw_url: str) -> "BangumiParseResult":
        try:
            if "/group/topic/" in raw_url:
                return await self._parse_group_topic(raw_url)
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

    async def _parse_group_topic(self, raw_url: str) -> "BangumiParseResult":
        topic = await BangumiGroupTopic.parse(
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
            # 主楼就是第 1 层 —— 用通用的位置标记标出来（各平台同一机制）
            position_label="#1" if topic.author_handle else "",
        )


def _refs(images: list) -> list[ImageRef]:
    return [ImageRef(url=i.url, thumb_url=i.url, width=i.width, height=i.height) for i in images]


__all__ = ["BangumiParseResult", "BangumiParser"]
