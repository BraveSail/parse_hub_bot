import asyncio
import html
import json
import re
from collections.abc import Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr
from urlextract import URLExtract

from ..types.platform import Platform

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"


def get_author_name(author: object, *fields: str) -> str:
    """Read a display name from an explicit author object, never from post content."""
    if isinstance(author, str):
        return author.strip()
    if not isinstance(author, Mapping):
        return ""
    for field in fields or (
        "full_name",
        "display_name",
        "nickname",
        "nickName",
        "screen_name",
        "name_show",
        "name",
        "user_name",
        "userName",
        "username",
        "unique_id",
        "uniqueId",
    ):
        value = author.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def format_author_label(name: str, handle: str = "") -> str:
    """把作者显示名与用户名拼成一行标签.

    - 两者都有且不同 → ``名字 @handle``
    - 两者相同 (忽略大小写与首尾空白, handle 的 ``@`` 前缀也会去掉) → 只留 ``@handle``
    - 只有一边 → 返回那一边; 都没有 → 空串
    """
    name = (name or "").strip()
    handle = (handle or "").strip().lstrip("@").strip()
    if not name:
        return f"@{handle}" if handle else ""
    if not handle:
        return name
    if name.casefold() == handle.casefold():
        return f"@{handle}"
    return f"{name} @{handle}"


# 各平台的作者主页地址模板. ``{handle}`` 用用户名, ``{id}`` 用平台数字/字符串 ID。
# 平台没有主页概念 (或只有 ID) 时就不列, 对应结果里 author_url 为空串。
PROFILE_URL_TEMPLATES: dict[Platform, str] = {
    Platform.BILIBILI: "https://space.bilibili.com/{id}",
    Platform.DOUBAN: "https://www.douban.com/people/{id}/",
    Platform.INSTAGRAM: "https://www.instagram.com/{handle}/",
    Platform.LINUXDO: "https://linux.do/u/{handle}",
    Platform.PIXIV: "https://www.pixiv.net/users/{id}",
    Platform.THREADS: "https://www.threads.com/@{handle}",
    Platform.TIKTOK: "https://www.tiktok.com/@{handle}",
    Platform.TWITTER: "https://x.com/{handle}",
    Platform.WEIBO: "https://weibo.com/u/{id}",
    Platform.YOUTUBE: "https://www.youtube.com/@{handle}",
    Platform.ZHIHU: "https://www.zhihu.com/people/{id}",
}


def profile_url(platform: Platform | None, handle: str = "", user_id: str = "") -> str:
    """作者主页地址; 平台没有模板或缺少所需字段时返回空串。"""
    template = PROFILE_URL_TEMPLATES.get(platform) if platform else None
    if not template:
        return ""
    values = {
        "handle": quote((handle or "").strip().lstrip("@"), safe=""),
        "id": quote(str(user_id or "").strip(), safe=""),
    }
    if any(not values[key] for key in values if f"{{{key}}}" in template):
        return ""
    return template.format(**values)


def format_author_link(name: str, handle: str = "", url: str = "") -> str:
    """作者标签 (名字 @handle); 给了主页地址时把 @handle 渲染成 HTML 链接。

    富文本 (rich message) 里 markdown 链接语法不生效, 会原样显示成 ``[文字](url)``,
    所以在这里统一用 ``<a href>``。
    """
    label = format_author_label(name, handle)
    clean = (handle or "").strip().lstrip("@").strip()
    if label and url and clean and f"@{clean}" in label:
        link = f'<a href="{html.escape(url, quote=True)}">@{html.escape(clean)}</a>'
        label = label.replace(f"@{clean}", link)
    return label


def format_quote_block(text: str, author: str = "") -> str:
    """把一段文本渲染成斜体的 Markdown 引用块, 文本为空时返回空串。

    不写 "回复/引用" 字样: 引用块本身已经表明关系。整块斜体, 作者行在前。
    """
    body = (text or "").strip()
    if not body:
        return ""
    lines = "\n".join(f"> *{line}*" if line.strip() else ">" for line in body.splitlines())
    head = f"> *{author}：*\n" if author else ""
    return f"{head}{lines}\n\n"


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    coro.close()
    raise RuntimeError("sync API cannot be called from a running event loop; use async API instead")


_url_extractor = URLExtract()


def match_url(text: str) -> str:
    """从文本中提取url"""
    if not text:
        return ""
    text = re.sub(r"(https?://)", r" \1", text)  # 协议前面增加空格, 方便提取
    url = _url_extractor.find_urls(text, only_unique=True)
    return url[0] if url else ""


class SecretCookie:
    def __init__(self, cookie: str | dict[str, Any] | None = None) -> None:
        self._cookie = self.normalize_cookie(cookie)

    def __bool__(self) -> bool:
        return bool(self._cookie)

    def __str__(self) -> str:
        if self._cookie is None:
            return ""
        return "; ".join([f"{k}={v}" for k, v in self._cookie.items()])

    def get_value(self) -> dict[str, str] | None:
        if self._cookie is None:
            return None
        return {key: value.get_secret_value() for key, value in self._cookie.items()}

    @staticmethod
    def normalize_cookie(v: str | dict[str, Any] | None) -> dict[str, SecretStr] | None:
        if v is None:
            return v
        if isinstance(v, dict):
            return {str(k).strip(): SecretStr("" if v is None else str(v).strip()) for k, v in v.items()}
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None

            if s.startswith("{") and s.endswith("}"):
                try:
                    data = json.loads(s)
                except Exception as e:
                    raise ValueError(f"cookie JSON解析失败: {e}") from e
                if not isinstance(data, dict):
                    raise ValueError("cookie JSON必须是对象类型")
                return {str(k).strip(): SecretStr("" if v is None else str(v).strip()) for k, v in data.items()}

            if s.lower().startswith("cookie:"):
                s = s[7:].strip()

            parts = [p.strip() for p in s.split(";") if p.strip()]
            result: dict[str, SecretStr] = {}
            for p in parts:
                if "=" not in p:
                    key = p.strip()
                    if key:
                        result[key] = SecretStr("")
                    continue
                k, val = p.split("=", 1)
                result[k.strip()] = SecretStr(val.strip())
            return result or None

        raise ValueError("cookie 必须是字符串、字典、JSON 或 None")


#: 各平台的时间字符串格式, 按出现频率排列
_DATETIME_FORMATS = (
    "%a %b %d %H:%M:%S %z %Y",  # Twitter/X: "Tue Oct 01 12:00:00 +0000 2026"
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

#: 超过这个数值就当成毫秒时间戳 (unix 秒到公元 5138 年才会达到)
_MILLISECOND_THRESHOLD = 100_000_000_000


def to_datetime(value: object) -> datetime | None:
    """把平台五花八门的时间字段归一化成带时区的 datetime。

    支持 unix 秒 / 毫秒 (数字或纯数字字符串)、ISO 8601 字符串、
    ``%a %b %d %H:%M:%S %z %Y`` 这类英文格式, 以及已经是 datetime 的值。
    识别不了时返回 None 而不是抛错 —— 元数据缺失不该让整条解析失败。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    seconds: float | None = None
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            seconds = float(text)
        else:
            parsed = _parse_datetime_text(text)
            return parsed

    if seconds is None or seconds <= 0:
        return None
    if seconds >= _MILLISECOND_THRESHOLD:
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def to_int(value: object) -> int | None:
    """把平台给的字符串计数 (如 twitter 的 ``views.count``) 归一化成 int。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None


def _parse_datetime_text(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _DATETIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            break
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
