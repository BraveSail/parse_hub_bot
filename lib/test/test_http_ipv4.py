"""HTTP 客户端默认**只用 IPv4**。

原因（2026-10-06 实测）：本机 DNS 是 IPv6 优先，而有些 CDN 资源在 IPv6 路径上被拒、
IPv4 正常 —— 推特一条视频：IPv4 → 200、IPv6 → 403（同客户端、同机器、同出口 IP）。
"""

from curl_cffi.const import CurlOpt

from parsehub.utils import http
from parsehub.utils.http import FORCE_IPV4


def _options(cls) -> dict:
    """取出构造时传给 curl_cffi 的 curl_options（拦掉真实构造）。"""
    captured: dict = {}

    def fake_init(self, **params):  # noqa: ANN001
        captured.update(params)

    original = http.AsyncSession.__init__
    http.AsyncSession.__init__ = fake_init
    try:
        cls()
    finally:
        http.AsyncSession.__init__ = original
    return captured.get("curl_options") or {}


def test_the_client_defaults_to_ipv4():
    opts = _options(http.AsyncClient)
    assert opts.get(CurlOpt.IPRESOLVE) == 1, f"默认应强制 IPv4: {opts}"
    assert FORCE_IPV4 == {CurlOpt.IPRESOLVE: 1}


def test_a_caller_can_override_it():
    """调用方显式给了就别覆盖（保留将来放开某个站点的余地）"""
    opts = _options(lambda: http.AsyncClient(curl_options={CurlOpt.IPRESOLVE: 0}))
    assert opts.get(CurlOpt.IPRESOLVE) == 0, opts


def test_other_curl_options_are_kept():
    opts = _options(lambda: http.AsyncClient(curl_options={CurlOpt.TIMEOUT: 7}))
    assert opts.get(CurlOpt.TIMEOUT) == 7
    assert opts.get(CurlOpt.IPRESOLVE) == 1, "额外选项不能把 IPv4 挤掉"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
