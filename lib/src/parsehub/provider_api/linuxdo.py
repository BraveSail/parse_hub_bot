"""linux.do（Discourse 论坛）解析。

数据走 Discourse 自带的 JSON 接口: ``/t/topic/<id>.json``（比抓 HTML 稳）。

⚠️ 该站点前置 Cloudflare，**必须同时满足两点**才能拿到数据（2026-10 实测）：

1. **浏览器 TLS 指纹** —— 本项目统一走 ``parsehub.utils.http``（curl_cffi, chrome150）。
   实测同一份 cookie 下 httpx 得到 403 挑战页、curl_cffi 得到 200。
2. **携带 cookie** —— 至少要有 ``cf_clearance``（Cloudflare 放行票）与 ``_forum_session``
   （Discourse 会话）。请在 ``platform_config.yaml`` 的 ``platforms.linuxdo.cookies`` 里配置
   浏览器里复制的完整 Cookie 头；``cf_clearance`` 会过期，失效后表现为 403。

不需要 cookie 的接口不存在 —— 匿名访问同样被 Cloudflare 拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from ..utils import http
from ..utils.helpers import to_int

TOPIC_API = "https://linux.do/t/topic/{topic_id}.json"

#: /t/<slug>/<id>、/t/topic/<id>、/t/<id> 三种形态
_TOPIC_URL_RES = (
    re.compile(r"^https?://linux\.do/t/(?:[^/]+/)?(\d+)"),
    re.compile(r"^https?://linux\.do/t/topic/(\d+)"),
)


class LinuxDoError(Exception):
    """linux.do 解析失败。"""


@dataclass
class LinuxDoImage:
    """帖子里的图片。"""

    url: str
    width: int = 0
    height: int = 0


@dataclass
class LinuxDoTopic:
    """一个主题（楼主帖 + 主题级统计）。"""

    topic_id: str = ""
    title: str = ""
    markdown_content: str = ""
    text_content: str = ""
    author_name: str = ""
    author_handle: str = ""
    published_at: str = ""
    view_count: int | None = None
    like_count: int | None = None
    reply_count: int | None = None
    tags: list[str] = field(default_factory=list)
    images: list[LinuxDoImage] = field(default_factory=list)
    is_sensitive: bool = False

    @staticmethod
    def get_topic_id(url: str) -> str:
        for pattern in _TOPIC_URL_RES:
            if match := pattern.search(url):
                return match.group(1)
        raise LinuxDoError("暂不支持该 linux.do 链接, 目前仅支持话题页 (/t/<话题>/<id>)")

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> LinuxDoTopic:
        topic_id = cls.get_topic_id(url)
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(
                    TOPIC_API.format(topic_id=topic_id),
                    headers={"Accept": "application/json"},
                )
        except http.HTTPError as e:
            raise LinuxDoError(f"请求 linux.do 失败: {e}") from e

        if response.status_code == 403:
            raise LinuxDoError("linux.do 需要有效的 cookie (含 cf_clearance), 请在平台配置里更新")
        if response.status_code == 404:
            raise LinuxDoError(f"话题 {topic_id} 不存在或不可见")
        response.raise_for_status()

        try:
            payload: dict[str, Any] = response.json()
        except Exception as e:  # noqa: BLE001 - 被 Cloudflare 拦时会返回 HTML
            raise LinuxDoError("linux.do 返回的不是 JSON, 多半是 Cloudflare 挑战页面 (cookie 失效或缺少指纹)") from e

        return cls._from_payload(payload, topic_id)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], topic_id: str) -> LinuxDoTopic:
        posts: list[dict[str, Any]] = (payload.get("post_stream") or {}).get("posts") or []
        first = next((p for p in posts if p.get("post_number") == 1), posts[0] if posts else None)
        if first is None:
            raise LinuxDoError("话题里没有可解析的内容")

        cooked: str = first.get("cooked") or ""
        soup = BeautifulSoup(cooked, "lxml")
        images = cls._extract_images(soup)
        cls._simplify(soup)
        markdown_content = cls._to_markdown(str(soup))

        tags = [str(t.get("name")) for t in (payload.get("tags") or []) if isinstance(t, dict) and t.get("name")]
        created_by = (payload.get("details") or {}).get("created_by") or {}
        author_handle = str(first.get("username") or created_by.get("username") or "")
        author_name = str(first.get("name") or created_by.get("name") or "")

        return cls(
            topic_id=str(payload.get("id") or topic_id),
            title=str(payload.get("title") or payload.get("fancy_title") or ""),
            markdown_content=markdown_content,
            text_content=soup.get_text("\n", strip=True),
            author_name=author_name,
            author_handle=author_handle,
            published_at=str(payload.get("created_at") or first.get("created_at") or ""),
            view_count=to_int(payload.get("views")),
            like_count=to_int(payload.get("like_count")),
            reply_count=to_int(payload.get("reply_count")),
            tags=tags,
            images=images,
            # 只认平台自己的标记: 标签里的 NSFW
            is_sensitive=any(t.casefold() == "nsfw" for t in tags),
        )

    @staticmethod
    def _simplify(soup: BeautifulSoup) -> None:
        """清理 Discourse 特有的包裹结构，避免转换出噪音。

        - ``div.lightbox-wrapper``：图片会作为媒体单独发送，正文里不再重复
        - ``details`` / ``summary``：富文本渲染不支持折叠，这里展开（保留内容、去掉外壳标题）
        - ``div.spoiler``：只保留内容
        """
        for wrapper in soup.find_all("div", class_="lightbox-wrapper"):
            wrapper.decompose()
        for details in soup.find_all("details"):
            details.unwrap()
        for summary in soup.find_all("summary"):
            summary.decompose()
        for spoiler in soup.find_all("div", class_="spoiler"):
            spoiler.unwrap()

    @staticmethod
    def _to_markdown(html: str) -> str:
        """HTML → markdown。"""
        if not html:
            return ""
        converter = MarkdownConverter(heading_style="ATX")
        markdown_content = str(converter.convert(html))
        # 去掉转换后的空行堆积与 markdownify 对普通文字里的下划线转义
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
        return markdown_content.strip()

    @staticmethod
    def _extract_images(soup: BeautifulSoup) -> list[LinuxDoImage]:
        """取帖子里的图片: lightbox 的 href 是原图, img 的宽高用来声明显示比例。"""
        images: list[LinuxDoImage] = []
        seen: set[str] = set()

        for img in soup.find_all("img"):
            src = str(img.get("src") or "")
            link = img.find_parent("a")
            original = str((link.get("href") if link else "") or src)
            url = original or src
            if not url or url in seen:
                continue
            seen.add(url)
            images.append(
                LinuxDoImage(
                    url=url,
                    width=to_int(img.get("width")) or 0,
                    height=to_int(img.get("height")) or 0,
                )
            )
        return images


__all__ = ["LinuxDoError", "LinuxDoImage", "LinuxDoTopic"]
