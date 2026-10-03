"""linux.do（Discourse 论坛）解析器。"""

from typing import Union

from ...provider_api.linuxdo import LinuxDoError, LinuxDoTopic
from ...types import (
    ImageParseResult,
    ImageRef,
    ParseError,
    Platform,
    RichTextParseResult,
)
from ...utils.helpers import profile_url
from ..base.base import BaseParser


class LinuxDoRichTextParseResult(RichTextParseResult):
    """linux.do 的图文主题（正文是 markdown）。"""


class LinuxDoImageParseResult(ImageParseResult):
    """linux.do 的纯图主题。"""


class LinuxDoParser(BaseParser):
    __platform__ = Platform.LINUXDO
    __supported_type__ = ["图文"]
    __match__ = r"^(http(s)?://)?linux\.do/t/.*"

    async def _do_parse(self, raw_url: str) -> Union["LinuxDoRichTextParseResult", "LinuxDoImageParseResult"]:
        try:
            topic = await LinuxDoTopic.parse(
                raw_url,
                proxy=self.proxy,
                cookie=self.cookie.get_value() if self.cookie else None,
            )
        except LinuxDoError as e:
            raise ParseError(str(e)) from e
        except Exception as e:
            raise ParseError(f"无法获取话题内容: {e}") from e

        media = [ImageRef(url=i.url, thumb_url=i.url, width=i.width, height=i.height) for i in topic.images]
        common = {
            "title": topic.title,
            "media": media,
            "author_name": topic.author_name,
            "author_handle": topic.author_handle,
            "author_url": profile_url(Platform.LINUXDO, topic.author_handle),
            "is_sensitive": topic.is_sensitive,
            "published_at": topic.published_at,
            "view_count": topic.view_count,
            "like_count": topic.like_count,
            "tags": topic.tags,
        }

        if topic.markdown_content:
            return LinuxDoRichTextParseResult(markdown_content=topic.markdown_content, **common)  # type: ignore[arg-type]
        return LinuxDoImageParseResult(content=topic.text_content, **common)  # type: ignore[arg-type]


__all__ = ["LinuxDoImageParseResult", "LinuxDoParser", "LinuxDoRichTextParseResult"]
