"""HTTP 封装层: 默认超时不能是"永不超时"。

curl_cffi 的 ``timeout`` 默认 ``None``, 源码里直接翻成 ``0 = indefinitely``;
httpx 的默认是 5 秒。迁移后如果沿用"不传就不设", 一次网络抖动就会让请求
永久挂起 —— bot 既不报错也不回消息。这里把"默认必须有超时"钉住。
"""

import time

from parsehub.utils import http
from parsehub.utils.http import DEFAULT_TIMEOUT


def test_default_timeout_is_bounded():
    """不传 timeout 也要有超时, 且是个合理的 (连接, 读) 组合"""
    assert DEFAULT_TIMEOUT is not None
    connect, read = DEFAULT_TIMEOUT
    assert 0 < connect <= 30
    assert 0 < read


def test_client_without_timeout_gets_the_default():
    client = http.AsyncClient()
    assert client.timeout == DEFAULT_TIMEOUT


def test_explicit_timeout_wins():
    assert http.AsyncClient(timeout=7).timeout == 7
    assert http.AsyncClient(timeout=(1, 2)).timeout == (1, 2)


def test_explicit_none_falls_back_to_the_default():
    """``timeout=None`` 在 httpx 里是"禁用超时", 这里不能照搬 —— 那正是卡死的原因"""
    assert http.AsyncClient(timeout=None).timeout == DEFAULT_TIMEOUT


def test_request_actually_times_out():
    """对一个不响应的地址, 要按时抛错而不是永远等待"""
    import asyncio

    async def run() -> float:
        started = time.monotonic()
        try:
            async with http.AsyncClient(timeout=(2, 3)) as client:
                await client.get("http://10.255.255.1:8080/nothing")
        except Exception:
            return time.monotonic() - started
        raise AssertionError("请求居然成功了, 这个用例的前提不成立")

    elapsed = asyncio.run(run())
    assert elapsed < 30, f"超时没生效, 等了 {elapsed:.1f} 秒"
