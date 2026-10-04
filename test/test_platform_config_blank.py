"""平台配置里的留空写法: 合法状态, 不该让 bot 起不来。

改配置时删掉 cookie 值只留一个 ``-`` (YAML 解析成 ``[None]``) 是很自然的写法,
以前会校验失败 → ``load_config`` 里 ``raise SystemExit(1)`` → **整个 bot 下线**
(2026-10-04 用户在 facebook 上就这么踩了一次)。
"""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.platform_config import Platform, PlatformsConfig


@pytest.mark.parametrize(
    ("label", "cookies"),
    [
        ("只留一个 -", [None]),
        ("空列表", []),
        ("空串", ["", "   "]),
        ("有值混着空", ["a=1", None, ""]),
    ],
)
def test_blank_cookies_are_treated_as_absent(label, cookies):
    """留空 == 没有这项配置 (退化为匿名), 不是配置错误"""
    p = Platform(cookies=cookies)
    assert p.cookies is None or all(p.cookies)
    assert p.roll_cookie() is None or p.roll_cookie() is not None


def test_real_cookies_are_kept():
    p = Platform(cookies=["a=1", None])
    assert len(p.cookies) == 1
    assert p.cookies[0].get_secret_value() == "a=1"


def test_blank_proxies_are_treated_as_absent():
    p = Platform(parser_proxies=[None], downloader_proxies=[])
    assert p.parser_proxies is None
    assert p.downloader_proxies is None
    assert p.roll_parser_proxy() is None
    assert p.roll_downloader_proxy() is None


def test_empty_platform_block_is_fine():
    p = Platform()
    assert p.cookies is None


def test_real_mistakes_still_get_caught():
    """留空要容忍, 但真写错 (类型不对/未知字段) 仍要拦下"""
    with pytest.raises(ValidationError):
        Platform(cookies=123)
    with pytest.raises(ValidationError):
        Platform(cookiez=["a=1"])


def test_a_broken_platform_does_not_take_the_whole_bot_down():
    """一份配置里有坏平台时: 跳过它, 其余照常, **进程不退出**"""
    cfg_file = Path(tempfile.mkdtemp()) / "platform_config.yaml"
    cfg_file.write_text(
        "platforms:\n"
        "  facebook:\n"
        "    cookies:\n"
        "    -\n"                        # 留空: 容忍
        "  twitter:\n"
        "    cookies:\n"
        "    - auth_token=ok\n"
        "  nosuchplatform:\n"            # 平台名不存在: 跳过
        "    cookies:\n"
        "    - x=1\n"
        "  instagram:\n"
        "    cookies: 123\n"             # 类型错: 跳过
        "    badfield: 1\n",
        encoding="utf-8",
    )
    cfg = PlatformsConfig.load_config(cfg_file)

    assert cfg.get("facebook").cookies is None        # 留空可用
    assert len(cfg.get("twitter").cookies or []) == 1  # 正常平台保留
    assert cfg.get("nosuchplatform") is None           # 拼错的平台没进来
    assert cfg.get("instagram") is None                # 坏配置没进来
