import asyncio
import html
import json
import re
from collections.abc import Coroutine, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import quote, urlparse

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
    # bgm 的日志标签页是**用户级**的（``/user/<uid>/blog/tag/<名>``），所以模板要 uid
    Platform.BANGUMI: "https://bgm.tv/user/{id}",
    Platform.BILIBILI: "https://space.bilibili.com/{id}",
    Platform.DOUBAN: "https://www.douban.com/people/{id}/",
    # sec_uid (短视频平台的用户标识, 与数字 uid 不同 —— 用 uid 打不开主页)
    Platform.DOUYIN: "https://www.douyin.com/user/{id}",
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


def profile_id_from_url(url: str) -> str:
    """从主页 URL 里取用户标识。

    给**只有 ID、没有 @用户名**的平台用 (B 站 ``space.bilibili.com/<mid>``、
    抖音 ``/user/<sec_uid>``、pixiv ``/users/<id>``) —— 那种平台的标识就在 URL 末段。

    用户要求 (原话「像B站这种id形式没有用户名的，换成 @uid」): 没有用户名时不要让
    ``@`` 那一段整个消失, 拿 ID 顶上。URL 没有可用段时返回空串。
    """
    try:
        path = urlparse(url or "").path.strip("/")
    except ValueError:  # 畸形 URL: 不该让作者行整行失败
        return ""
    return path.rsplit("/", 1)[-1] if path else ""


def format_author_link(name: str, handle: str = "", url: str = "") -> str:
    """作者标签: **显示名做成超链接**, ``@用户名`` 作为**等宽下角标**。

    用户要求 (原话「把作者名 @用户名 改成 作者名超链接，@用户名弄成 下角标」+
    「这个下角标能不能调成灰色就是等宽那种」): 名字是读者要点进去的,
    而 ``@handle`` 只是标识 —— 占位比信息值钱, 所以压成小字附在名字后面。

    写法说明 (都真机实测过):

    - **超链接必须用 HTML ``<a href>``**: 富文本里 markdown 链接语法不生效,
      会原样显示成 ``[文字](url)``。
    - **``@handle`` = 等宽、不带角标**: ``<code>@handle</code>`` (用户定稿)。
      历程（都真发过、读回过服务端节点 —— 记下来免得再绕一圈）:

      | 写法 | 服务端节点 | 可点? | 观感 |
      | --- | --- | --- | --- |
      | ``<sub>@h</sub>`` | ``textMention`` | **可点** | 常规小字但**下沉** |
      | ``<sub><code>@h</code></sub>`` | ``textCode`` | 不可点 | 等宽 + 下沉 |
      | **``<code>@h</code>``** | ``textCode`` | 不可点 | **等宽、同基线** ← 现行 |
      | 整条 ``skip_entity_detection`` | ``textPlain`` | 不可点 | 常规同基线 (要自己链化裸 URL, 已撤) |

      ⇒ 用户结论: ``<sub>`` 会把 handle **压到名字基线以下**、看着不齐; 所以**不要角标**。
      ``<code>`` 恰好同时满足"不可点"(压掉 ``@提及`` 自动识别) 与"同基线"。
    - 名字与 ``@handle`` 之间**空一格**。

    只有一个名字 (没有 ``@handle``, 或两者相同) 时, 那一个仍然做成链接 ——
    否则整行不可点。没有主页地址时退回 ``format_author_label`` 的纯文本形态。
    """
    display = (name or "").strip()
    clean = (handle or "").strip().lstrip("@").strip()
    if not url:
        return format_author_label(name, handle)
    href = html.escape(url, quote=True)
    # 标识: 优先真实用户名; 没有就取主页 URL 末段当 ID (B 站 mid / 抖音 sec_uid / pixiv id)
    tag = clean or profile_id_from_url(url)
    # 名字与标识是两个不同的东西: 名字可点, 标识是后面的等宽标识
    if display and tag and display.casefold() != tag.casefold():
        # 等宽、**不带角标** (`<sub>` 会把 handle 压到名字基线以下, 用户不要):
        # `<code>` 同时满足"不可点击"(压掉 @提及 自动识别) 与"同基线"。
        handle_markup = f"<code>@{html.escape(tag)}</code>"
        return f'<a href="{href}">{html.escape(display)}</a> {handle_markup}'
    # 只有一边 (或两边相同): 就一个, 做成链接。
    # 相同的情况沿用既有约定 —— 显示 ``@标识`` 形态 (不因为这次改动改语义)
    text = f"@{tag}" if tag else display
    return f'<a href="{href}">{html.escape(text)}</a>' if text else ""


def format_quote_block(text: str, author: str = "", *, sign_only: bool = False) -> str:
    """把一段文本渲染成引用块, 文本为空时返回空串。

    :param sign_only: 文本为空时**仍输出署名行**。给"被引用对象没有文字、但有媒体"
        的场景用 (纯图楼层、纯图转发动态) —— 那种情况下引用块只有署名, 图片由调用
        方按引用块媒体的通道接进块内 (``attach_quote_media``), 一块只有署名的引用块
        照样能带图。**默认 False**: 没有媒体配套的调用方 (threads) 拿到的空引用块
        是一行孤零零的署名, 不如不显示。

    不写 "回复/引用" 字样: 引用块本身已经表明关系。作者行在前。

    **不用斜体**（用户: 「引用/回复 不是斜体」）—— 以前整块包 ``<i>``，现在作者行与
    正文都按原样输出，块内不加任何强调。

    ⚠️ 块内 markdown **不解析**（富文本的 blockquote 路径），所以调用方若确实需要
    强调，只能用 HTML（``<b>`` / ``<i>``）；但作者行与正文默认不加。
    """
    body = (text or "").strip()
    # 署名不加冒号 (与作者行统一 —— 用户要求「取消冒号」)
    head = f"> {author}\n" if author else ""
    if not body:
        return f"{head}\n" if (head and sign_only) else ""
    lines = "\n".join(f"> {line}" if line.strip() else ">" for line in body.splitlines())
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


#: 允许跟在链接后面的句读 (句末加句号很常见, 不该因此漏解析)
_TRAILING_PUNCTUATION = "。，、！？；：,.!?;:"


#: 手动开关标记 (独立 token): 让本次结果把正文折起来、摘要标 ⚠️
#: 手动开关: 让本条结果把内容折起来。**所有标记等价**, 用户按语义挑一个 ——
#: ``#nsfw`` / ``#r18`` / ``#色色`` / ``#不可以色色`` 不宜公开, ``#spoiler`` / ``#劇透`` 剧透。
#:
#: 匹配**大小写不敏感**(``#R18`` / ``#NSFW`` 都算), 但返回的是用户写的原形,
#: 摘要照原样显示。简体 ``#剧透`` 一并认 —— 同一个词的两种写法都该触发。
SPOILER_FLAGS = ("#nsfw", "#spoiler", "#r18", "#劇透", "#剧透", "#色色", "#不可以色色")
#: 查表用的小写集合; 命中后返回的是原 token (保留用户写的大小写)
_SPOILER_LOOKUP = frozenset(flag.lower() for flag in SPOILER_FLAGS)
#: 无标记时的折叠摘要兜底 (正常总有一个标记, 摘要是 ``⚠️ <标记>``)
SPOILER_FOLD_SUMMARY = "⚠️"


def strip_spoiler_flag(text: str | None) -> tuple[str, str]:
    """剥掉独立的手动开关, 返回 (去掉标记后的文本, **命中的标记或空串**)。

    **必须是独立 token**: ``#nsfwxxx``、URL 里的片段都不算 —— 用户在链接后面
    空一格再写, 这样 URL 本体完全不变 (往 URL 里塞参数会被参数清理逻辑摘掉,
    加路径后缀又会污染缓存 key 与 provider 的路径解析)。

    返回命中的**标记本身**(而不是 bool) 是因为摘要要显示它: 用 ``#nsfw`` 触发就
    显示 ``⚠️ #nsfw``, 让读者一眼知道是被标为不宜公开还是剧透。
    **大小写不敏感**(``#R18`` / ``#NSFW`` 都算), 但返回值保持用户写的原形 ——
    摘要与用户输入一致, 不做规范化。
    """
    if not text:
        return "", ""
    hit = ""
    kept: list[str] = []
    for token in text.split():
        if token.lower() in _SPOILER_LOOKUP:
            hit = hit or token
        else:
            kept.append(token)
    return " ".join(kept), hit


def url_only_message_urls(text: str | None, *, ignore_mentions: Sequence[str] = ()) -> list[str]:
    """消息**整条都是链接**时返回这些链接, 否则返回空列表。

    用于"只解析纯链接消息"的场景 (群里有人随口提到链接不该被解析):
    按空白切分后要求每个片段**本身就是一个完整链接** —— 夹在文字里、被括号或
    表情包住、混在分享文案里的都不算。允许多个链接 (一行一个或空格分隔);
    链接末尾的句读会被去掉 (``https://x.com/...。`` 仍算纯链接)。

    :param ignore_mentions: 先剔除的 @提及 —— guest 场景必须传 bot 自己的用户名:
        guest 靠"消息里提到 bot"触发, 不剔掉的话 ``@bot <链接>`` 永远不算纯链接。
    """
    if not text:
        return []
    for mention in ignore_mentions:
        name = (mention or "").strip().lstrip("@").strip()
        if name:
            text = re.sub(rf"@{re.escape(name)}\b", " ", text)
    # 手动开关 (/s) 也要先剔掉, 否则 "<链接> /s" 永远不算纯链接
    text, _ = strip_spoiler_flag(text)
    tokens = [token.rstrip(_TRAILING_PUNCTUATION) for token in text.split()]
    if not tokens or any(not token for token in tokens):
        return []
    # 抽出的链接必须与片段完全一致: 不一致说明片段里还夹着别的内容
    if any(match_url(token) != token for token in tokens):
        return []
    return tokens


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


def to_datetime(value: object, *, default_tz: tzinfo = UTC) -> datetime | None:
    """把平台五花八门的时间字段归一化成带时区的 datetime。

    支持 unix 秒 / 毫秒 (数字或纯数字字符串)、ISO 8601 字符串、
    ``%a %b %d %H:%M:%S %z %Y`` 这类英文格式, 以及已经是 datetime 的值。
    识别不了时返回 None 而不是抛错 —— 元数据缺失不该让整条解析失败。

    :param default_tz: **字符串里没带时区时按哪个时区解释**。默认 UTC ——
        大部分平台的接口给的就是 UTC。抓网页的平台例外: 页面上的时间是**站点
        本地时间**(bgm 是北京时间), 按 UTC 解释会让时间整整差 8 小时
        (用户报「时间好像有问题？多 8 小时」), 那种平台要显式传自己的时区。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=default_tz)

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
            parsed = _parse_datetime_text(text, default_tz=default_tz)
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


def _parse_datetime_text(text: str, *, default_tz: tzinfo = UTC) -> datetime | None:
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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)
