"""测试用的假响应与打桩工具。

响应对象不依赖具体 HTTP 库（curl_cffi 的 Response 绑定 curl handle，无法像 httpx 那样
直接构造），provider 实际只用 `status_code` / `text` / `json()` / `headers` / `raise_for_status()`，
这里按这几项做一个鸭子类型的替身。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from parsehub.utils import http


class FakeResponse:
    """最小的假响应。"""

    def __init__(
        self,
        status_code: int = 200,
        *,
        text: str = "",
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json_data = json_data

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("response has no json body")
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise http.HTTPStatusError(f"HTTP {self.status_code}")


@contextmanager
def patch_async_get(response: FakeResponse | list[FakeResponse]):
    """把统一 HTTP 客户端的 get 打桩成固定响应（传列表时按调用顺序依次返回）。"""
    if isinstance(response, list):
        mock = AsyncMock(side_effect=list(response))
    else:
        mock = AsyncMock(return_value=response)
    with patch.object(http.AsyncClient, "get", new=mock):
        yield mock


__all__ = ["FakeResponse", "patch_async_get"]
