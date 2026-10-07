"""bgm.tv（Bangumi 番组计划）日志解析器。

只覆盖**日志**（``/blog/<id>``）。小组话题（``/group/topic/<id>``）页面结构不同构
（正文在 ``.topic_content``、楼层在 ``.postTopic``、没有 ``h1.title``），要做得另行设计。
条目页（``/subject/<id>``）由官方 API 覆盖，不需要抓网页。
"""

from ...provider_api.bangumi import BangumiBlog, BangumiError
from ...types import ImageRef, ParseError, Platform, RichTextParseResult
from ...utils.helpers import profile_url
from ..base.base import BaseParser


class BangumiParseResult(RichTextParseResult):
    """bgm.tv 日志（正文是 markdown）。"""

    #: 图片**已从正文里抽走**（见 provider），必须下载后当附件发送 ——
    #: 不置位的话流水线会跳过下载，图片就彻底丢了（与 linux.do 同一理由）。
    requires_media_download = True


class BangumiParser(BaseParser):
    __platform__ = Platform.BANGUMI
    __supported_type__ = ["图文"]
    # 锚定 /blog/<数字>：bgm.tv 与 bangumi.tv 两个域名都有人用
    __match__ = r"^(http(s)?://)?(bgm|bangumi)\.tv/blog/\d+"

    async def _do_parse(self, raw_url: str) -> "BangumiParseResult":
        try:
            blog = await BangumiBlog.parse(
                raw_url,
                proxy=self.proxy,
                cookie=self.cookie.get_value() if self.cookie else None,
            )
        except BangumiError as e:
            raise ParseError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"无法获取日志内容: {e}") from e

        media = [ImageRef(url=i.url, thumb_url=i.url, width=i.width, height=i.height) for i in blog.images]
        return BangumiParseResult(
            title=blog.title,
            markdown_content=blog.markdown_content,
            media=media,
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


__all__ = ["BangumiParseResult", "BangumiParser"]
