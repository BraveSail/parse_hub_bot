import math
import re
from dataclasses import dataclass
from typing import Any, cast

from bs4 import BeautifulSoup

from ..utils import http

ILLUST_API = "https://www.pixiv.net/ajax/illust/{}"
PAGES_API = "https://www.pixiv.net/ajax/illust/{}/pages"

REFERER = "https://www.pixiv.net/"
"""pixiv 的 ajax 接口和 i.pximg.net 图床都校验 Referer, 不带会 403"""

MASTER_LONG_EDGE = 1200
"""pixiv master1200 版本的最大边长 (原图会被缩到长边 1200, 存成 jpg)"""

# /artworks/<id> (可能带语言前缀, 如 /en/artworks/<id>), 以及旧式 member_illust.php?illust_id=<id>
ILLUST_URL_RES = (
    r"pixiv\.net/(?:[a-z]{2}(?:-[a-z]{2})?/)?artworks/(\d+)",
    r"pixiv\.net/member_illust\.php\?(?:[^#]*&)?illust_id=(\d+)",
)

ORIGINAL_URL_RE = re.compile(r"^https://i\.pximg\.net/img-original/img/(.+)_p(\d+)\.[a-zA-Z]+$")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class PixivError(Exception):
    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


@dataclass
class PixivImage:
    url: str
    thumb_url: str | None = None
    width: int = 0
    height: int = 0
    ext: str = "jpg"


@dataclass
class PixivIllust:
    illust_id: str
    title: str
    author_name: str
    author_id: str
    tags: list[str]
    description: str
    images: list[PixivImage]
    page_count: int
    is_r18: bool
    create_date: str
    user_account: str = ""
    """作者的用户名 (@ 后面的部分), 与显示名 userName 可能不同"""
    view_count: int = 0
    like_count: int = 0

    @classmethod
    def parse(
        cls,
        data: dict[str, Any],
        pages: list[dict[str, Any]],
        *,
        illust_id: str = "",
    ) -> "PixivIllust":
        raw_tags = (data.get("tags") or {}).get("tags") or []
        tags = [str(t["tag"]) for t in raw_tags if isinstance(t, dict) and t.get("tag")]

        images = [img for page in pages if (img := _parse_image(page))]

        # urls 为空说明是 R-18 作品: pixiv 只对登录用户返回整组图片地址
        if not images:
            raise PixivError("该作品需要登录才能查看 (R-18/受限内容), 请为 pixiv 配置 cookie")

        return cls(
            illust_id=str(data.get("illustId") or illust_id),
            title=str(data.get("illustTitle") or data.get("title") or ""),
            author_name=str(data.get("userName") or ""),
            author_id=str(data.get("userId") or ""),
            tags=tags,
            description=_html_to_text(data.get("description") or data.get("illustComment") or ""),
            images=images,
            page_count=int(data.get("pageCount") or len(images)),
            is_r18=int(data.get("xRestrict") or 0) > 0,
            create_date=str(data.get("createDate") or ""),
            user_account=str(data.get("userAccount") or ""),
            view_count=int(data.get("viewCount") or 0),
            like_count=int(data.get("likeCount") or 0),
        )


IMAGE_EXTS = ("jpg", "jpeg", "png", "gif", "webp")


def _guess_ext(url: str) -> str:
    """后缀跟着 URL 走 (master1200 恒为 jpg, 未转换的原图可能是 png/gif)"""
    name = url.split("?", 1)[0].rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext if ext in IMAGE_EXTS else "jpg"


def _to_master_url(url: str) -> str:
    """原图链接 -> master1200 (长边 1200 的 jpg)

    原图可能非常大 (实测 6000x6000 / 7MB), Telegram 抓它当照片时稳定失败
    (400 failed to get HTTP URL content); master1200 只有 ~1MB, 同尺寸下 TG 能正常抓取。
    其他平台的 url 也都是中等尺寸 (twitter 的 large / instagram 的 display_url) 而非原图。
    """
    if not (matched := ORIGINAL_URL_RE.match(url)):
        return url
    path, page = matched.groups()
    return f"https://i.pximg.net/img-master/img/{path}_p{page}_master1200.jpg"


def _master_size(width: int, height: int) -> tuple[int, int]:
    """master1200 的尺寸: 长边缩到 1200, 短边按比例 (pixiv 是向上取整)

    实测 1968x2664 -> 887x1200, 6000x6000 -> 1200x1200。
    尺寸只用于告诉 Telegram 结果卡片多大, 差 1px 无影响。
    """
    longest = max(width, height)
    if longest <= MASTER_LONG_EDGE:
        return width, height
    scale = MASTER_LONG_EDGE / longest
    return math.ceil(width * scale), math.ceil(height * scale)


def _parse_image(page: dict[str, Any]) -> PixivImage | None:
    urls = page.get("urls") or {}
    original = urls.get("original") or urls.get("regular")
    if not original:
        return None
    width = int(page.get("width") or 0)
    height = int(page.get("height") or 0)
    thumb = urls.get("thumb_mini") or urls.get("small")
    # 原图过大就换成 master1200; 本来就小的图保持原样 (小图的 master1200 未必存在)
    if max(width, height) > MASTER_LONG_EDGE:
        url = _to_master_url(str(original))
        width, height = _master_size(width, height)
    else:
        url = str(original)
    return PixivImage(
        url=url,
        thumb_url=str(thumb) if thumb and thumb != url else None,
        width=width,
        height=height,
        ext=_guess_ext(url),
    )


def _html_to_text(html: str) -> str:
    """pixiv 的简介是 HTML 片段, 换行用 ``<br />``; 链接文本本身就是可读 URL"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # 折叠 pixiv 常见的连续空行
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


class Pixiv:
    def __init__(self, proxy: str | None = None, cookie: dict[str, str] | None = None):
        self.proxy = proxy
        self.cookie = cookie

    async def parse(self, url: str) -> PixivIllust:
        illust_id = self.get_illust_id(url)
        headers = {"User-Agent": UA, "Referer": REFERER}
        async with http.AsyncClient(proxy=self.proxy, cookies=self.cookie, timeout=30) as cli:
            illust = await self._fetch(cli, ILLUST_API.format(illust_id), illust_id, headers)
            pages = await self._fetch_pages(cli, illust_id, headers)
        return PixivIllust.parse(illust, pages, illust_id=illust_id)

    async def _fetch(
        self,
        cli: http.AsyncClient,
        url: str,
        illust_id: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        result = await cli.get(url, headers=headers)
        if result.status_code != 200:
            raise PixivError(f"获取作品信息失败: HTTP {result.status_code}")
        payload = cast(dict[str, Any], result.json())
        if payload.get("error"):
            raise PixivError(f"作品 {illust_id} 不存在或不可见")
        return cast(dict[str, Any], payload.get("body") or {})

    async def _fetch_pages(
        self,
        cli: http.AsyncClient,
        illust_id: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        result = await cli.get(PAGES_API.format(illust_id), headers=headers)
        if result.status_code != 200:
            raise PixivError(f"获取作品图片失败: HTTP {result.status_code}")
        payload = cast(dict[str, Any], result.json())
        if payload.get("error"):
            raise PixivError("该作品需要登录才能查看 (R-18/受限内容), 请为 pixiv 配置 cookie")
        return cast(list[dict[str, Any]], payload.get("body") or [])

    @staticmethod
    def get_illust_id(url: str) -> str:
        for pattern in ILLUST_URL_RES:
            if match := re.search(pattern, url):
                return match.group(1)
        raise PixivError("暂不支持该 pixiv 链接, 目前仅支持插画/漫画作品页 (artworks/<id>)")
