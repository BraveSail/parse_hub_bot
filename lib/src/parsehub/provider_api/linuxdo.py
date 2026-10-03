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
#: 带楼层号: Discourse 返回以该楼层为中心的窗口
FLOOR_API = "https://linux.do/t/{topic_id}/{post_number}.json"

#: /t/topic/<id>[/<楼层>]、/t/<slug>/<id>[/<楼层>]、/t/<id>[/<楼层>]
#: slug 不以数字开头, 否则 /t/2979226/11 会被误当成 slug=2979226、id=11
_TOPIC_URL_RE = re.compile(r"^https?://linux\.do/t/(?:[^\d/][^/]*/)?(\d+)(?:/(\d+))?")


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
    def _split_url(url: str) -> tuple[str, str]:
        """返回 (话题 ID, 楼层号)；楼层号为空串表示没指定。"""
        if match := _TOPIC_URL_RE.match(url):
            return match.group(1), match.group(2) or ""
        raise LinuxDoError("暂不支持该 linux.do 链接, 目前仅支持话题页 (/t/<话题>/<id>)")

    @staticmethod
    def get_topic_id(url: str) -> str:
        return LinuxDoTopic._split_url(url)[0]

    @staticmethod
    def get_post_number(url: str) -> str:
        """URL 里指定的楼层号（``/t/topic/<id>/11`` → ``11``）；没指定返回空串。

        带楼层号时解析那一层（用户分享的常常是某个回复），不带则解析楼主帖。
        """
        return LinuxDoTopic._split_url(url)[1]

    @classmethod
    async def parse(
        cls,
        url: str,
        proxy: str | None = None,
        cookie: dict[str, str] | None = None,
    ) -> LinuxDoTopic:
        topic_id, post_number = cls._split_url(url)
        # 带楼层号时请求该楼层（Discourse 会返回以它为中心的窗口），否则请求楼主帖
        api = FLOOR_API.format(topic_id=topic_id, post_number=post_number) if post_number else TOPIC_API.format(
            topic_id=topic_id
        )
        try:
            async with http.AsyncClient(proxy=proxy, cookies=cookie, timeout=30) as client:
                response = await client.get(api, headers={"Accept": "application/json"})
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

        return cls._from_payload(payload, topic_id, post_number=post_number)

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], topic_id: str, post_number: str = "") -> LinuxDoTopic:
        """组装主题。

        ``post_number`` 指定楼层时取那一层（用户分享的链接常带楼层号，那一层才是他要看的内容）；
        没指定则取楼主帖。带楼层号的 URL 返回的帖子流**不含 1 楼**，所以不能盲目回落到第一项 ——
        那会取到窗口里的其它楼层（症状：明明楼层里有图，解析出来没媒体）。
        """
        posts: list[dict[str, Any]] = (payload.get("post_stream") or {}).get("posts") or []
        wanted = int(post_number) if post_number.isdigit() else 1
        first = next((p for p in posts if p.get("post_number") == wanted), None)
        if first is None and post_number:
            raise LinuxDoError(f"话题 {topic_id} 里没有第 {post_number} 楼")
        if first is None:
            first = posts[0] if posts else None
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
        - ``img.emoji`` / ``img.avatar``：直接去掉 —— emoji 的 alt 是 ``:name:`` 形式,
          留在正文里既不美观也不是原样文本; 头像则是引用回复头部的装饰
        """
        for tag in soup.find_all("img", class_=["emoji", "avatar"]):
            tag.decompose()
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
        # 去掉转换后的空行堆积（删掉表情/头像后可能多出空段落）
        markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
        return markdown_content.strip()

    @staticmethod
    def _extract_images(soup: BeautifulSoup) -> list[LinuxDoImage]:
        """取帖子里的图片: lightbox 的 href 是原图, img 的宽高用来声明显示比例。"""
        images: list[LinuxDoImage] = []
        seen: set[str] = set()

        for img in soup.find_all("img"):
            classes = img.get("class") or []
            # 表情 (img.emoji) 与头像 (img.avatar, 引用回复里那个 24x24 小图) 都不是帖子媒体
            if "emoji" in classes or "avatar" in classes:
                continue
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
