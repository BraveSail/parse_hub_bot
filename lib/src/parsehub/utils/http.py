"""统一的 HTTP 客户端。

底层用 **curl_cffi**（真实浏览器 TLS 指纹），对外保持既有的构造风格与异常命名，
这样各个 provider 不必知道底层换成了什么：

    from ...utils import http
    async with http.AsyncClient(proxy=self.proxy, cookies=self.cookie, timeout=30) as cli:
        resp = await cli.get(url, headers=headers)
        resp.raise_for_status()

为什么要浏览器指纹：部分站点（如 linux.do 走 Cloudflare）会按 TLS 指纹拒绝普通客户端，
实测同一份 cookie 下 `httpx → 403`、`curl_cffi(chrome150) → 200`。
"""

from __future__ import annotations

from typing import Any

from curl_cffi.requests import AsyncSession, Cookies, Headers
from curl_cffi.requests import exceptions as _curl_errors

#: 浏览器指纹 —— 集中一处，升级只改这里（curl_cffi 0.16 支持到 chrome150）
IMPERSONATE = "chrome150"

# ── 异常别名 ─────────────────────────────────────────────────────────────
# curl_cffi 的异常粒度比 httpx 粗（NetworkError/RemoteProtocolError/ReadError 都归到少数几类），
# 这里映射成调用点已经在用的名字，语义都是"网络/协议失败，可重试或报错"。
HTTPError = _curl_errors.RequestException
"""请求失败的基类（对应原来的 httpx.HTTPError）。"""
HTTPStatusError = _curl_errors.HTTPError
"""raise_for_status() 抛出的状态码错误，带 .response。"""
TimeoutException = _curl_errors.Timeout
ReadTimeout = _curl_errors.ReadTimeout
NetworkError = _curl_errors.ConnectionError
ConnectTimeout = _curl_errors.ConnectionError
RemoteProtocolError = _curl_errors.RequestException
ReadError = _curl_errors.ReadTimeout
RequestError = _curl_errors.RequestException
TooManyRedirects = _curl_errors.TooManyRedirects

# ── 类型占位（原来写 httpx.Proxy / httpx.Timeout / httpx.Headers 的地方）────
Proxy = str | Any
Timeout = float


class Limits:
    """连接池上限的占位（curl_cffi 用 max_clients 表达）。"""

    def __init__(
        self,
        max_keepalive_connections: int | None = None,
        max_connections: int | None = None,
        keepalive_expiry: float | None = None,
    ) -> None:
        self.max_keepalive_connections = max_keepalive_connections
        self.max_connections = max_connections
        self.keepalive_expiry = keepalive_expiry


class AsyncClient(AsyncSession):
    """``curl_cffi.AsyncSession`` 的外壳，构造参数与原来的 httpx 用法一一对应。"""

    def __init__(
        self,
        *,
        proxy: Any = None,
        proxies: Any = None,
        cookies: Any = None,
        headers: Any = None,
        timeout: Timeout | None = None,
        follow_redirects: bool | None = None,
        limits: Any = None,
        **kwargs: Any,
    ) -> None:
        params: dict[str, Any] = {"impersonate": IMPERSONATE}
        params.update(kwargs)

        target = proxy if proxy is not None else proxies
        if target:
            # httpx 收单个代理字符串；curl_cffi 要 {scheme: url}。也接受 httpx.Proxy 这类带 .url 的对象
            url = getattr(target, "url", target)
            params["proxies"] = url if isinstance(url, dict) else {"all": str(url)}
        if cookies is not None:
            params["cookies"] = cookies
        if headers is not None:
            params["headers"] = headers
        if timeout is not None:
            params["timeout"] = timeout
        if follow_redirects is not None:
            # httpx 默认不跟随重定向，curl_cffi 默认跟随 —— 必须显式对齐，
            # 否则原本依赖"不跟随"的地方（如下载器的 Range 探测）会静默改变行为
            params["allow_redirects"] = follow_redirects
        if limits is not None:
            max_conn = getattr(limits, "max_connections", None)
            if max_conn:
                params.setdefault("max_clients", max_conn)

        super().__init__(**params)

    async def request(self, method: str, url: str, **kwargs: Any):  # type: ignore[override]
        """请求级也接受 ``follow_redirects``（httpx 的写法），翻译成 curl_cffi 的 ``allow_redirects``。"""
        if "follow_redirects" in kwargs:
            kwargs["allow_redirects"] = kwargs.pop("follow_redirects")
        return await super().request(method=method, url=url, **kwargs)


def new_client(**kwargs: Any) -> AsyncClient:
    """构造客户端的简写（需要显式调用点时用）。"""
    return AsyncClient(**kwargs)


__all__ = [
    "IMPERSONATE",
    "AsyncClient",
    "ConnectTimeout",
    "Cookies",
    "HTTPError",
    "HTTPStatusError",
    "Headers",
    "Limits",
    "NetworkError",
    "Proxy",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "RequestError",
    "Timeout",
    "TimeoutException",
    "TooManyRedirects",
    "new_client",
]
