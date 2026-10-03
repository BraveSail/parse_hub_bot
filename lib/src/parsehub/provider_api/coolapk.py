from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from markdown import markdown
from markdownify import MarkdownConverter

from ..utils.helpers import UA, get_author_name


@dataclass
class Coolapk:
    title: str = ""
    markdown_content: str = ""
    text_content: str = ""
    imgs: list[str] | None = None
    author_name: str = ""

    @classmethod
    async def parse(cls, url: str, proxy: str | None = None) -> "Coolapk":
        async with httpx.AsyncClient(headers={"User-Agent": UA}, proxy=proxy) as client:
            result = await client.get(url)
        soup = BeautifulSoup(result.text, "lxml")
        author = soup.select_one(".feed-user-name, .feed-username, .user-name, .username")
        author_name = author.get_text(strip=True) if author else ""
        if not author_name and (meta := soup.find("meta", {"name": "author"})):
            author_name = get_author_name(meta.get("content"))
        # 酷安网页版不加载实况照片
        title_element = soup.find(class_="message-title")
        if title_element and (title := title_element.text.strip()):
            content = soup.find(class_="feed-article-message")
            if content is None:
                if "s=" in url or "shareKey=" in url:
                    raise ValueError("获取内容失败")
                raise ValueError("获取内容失败, 分享时请保留 shareKey 或 s 参数")
            markdown_content = MarkdownConverter(heading_style="ATX").convert(str(content))
            text_content = "".join(BeautifulSoup(markdown(markdown_content), "lxml").find_all(string=True))
            imgs = [f"https:{i['src']}" for i in content.find_all("img", {"class": "message-image"})]
            return cls(title, markdown_content, text_content, imgs, author_name=author_name)

        feed_element = soup.find(class_="feed-message")
        if feed_element and (feed_content := feed_element.text.strip()):
            message_image_group = soup.find(class_="message-image-group")
            imgs = [f"https:{i['src']}" for i in message_image_group.find_all("img")] if message_image_group else []
            return cls("", "", feed_content, imgs, author_name=author_name)

        raise ValueError("获取内容失败, 分享时请保留 shareKey 或 s 参数")
