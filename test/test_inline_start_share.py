"""inline 多图 → /start 深链发送的单测。

覆盖三层：token 映射（services.inline_share）、switch_pm 组装（plugins.parse.inline）、
/start payload 分支（plugins.start）。
"""

import asyncio
import re
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugins import start as start_module
from plugins.parse.inline import build_switch_pm, count_inline_media
from services.inline_share import InlineStartLinkService

# ── 1. token 映射（services.inline_share）────────────────────────────────


def test_token_is_url_safe_and_short():
    token = asyncio.run(InlineStartLinkService().register("https://example.com/a"))
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token)
    assert len(token) == 22  # token_urlsafe(16) → 22 字符


def test_resolve_roundtrip():
    svc = InlineStartLinkService()
    url = "https://www.pixiv.net/artworks/149431603"
    token = asyncio.run(svc.register(url))
    assert asyncio.run(svc.resolve(token)) == url


def test_resolve_unknown_returns_none():
    assert asyncio.run(InlineStartLinkService().resolve("nope")) is None


def test_resolve_expired_returns_none():
    svc = InlineStartLinkService(ttl=0.05)
    token = asyncio.run(svc.register("https://example.com/x"))
    time.sleep(0.1)
    assert asyncio.run(svc.resolve(token)) is None


def test_overflow_evicts_oldest():
    svc = InlineStartLinkService(maxsize=2)
    t1 = asyncio.run(svc.register("https://example.com/1"))
    t2 = asyncio.run(svc.register("https://example.com/2"))
    t3 = asyncio.run(svc.register("https://example.com/3"))
    assert asyncio.run(svc.resolve(t1)) is None
    assert asyncio.run(svc.resolve(t2)) == "https://example.com/2"
    assert asyncio.run(svc.resolve(t3)) == "https://example.com/3"


# ── 2. switch_pm 组装（plugins.parse.inline）─────────────────────────────


def test_count_inline_media():
    assert count_inline_media(None) == 0
    assert count_inline_media([]) == 0
    assert count_inline_media(object()) == 1  # 单个媒体对象（非序列）
    assert count_inline_media(["a", "b", "c"]) == 3


def test_switch_pm_absent_for_single_media():
    assert asyncio.run(build_switch_pm(1, "https://example.com/a", "zh-hans")) == ("", "")
    assert asyncio.run(build_switch_pm(0, "https://example.com/a", "zh-hans")) == ("", "")


def test_switch_pm_present_for_multi_media():
    from services import inline_start_link

    url = "https://www.pixiv.net/artworks/149431603"
    text, param = asyncio.run(build_switch_pm(5, url, "zh-hans"))
    assert text == "发送全部 5 项"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", param)
    assert asyncio.run(inline_start_link.resolve(param)) == url


def test_switch_pm_text_is_nonempty_for_other_locale():
    # 未 build 翻译时回退源文，build 后是目标语言；两种情况都应为非空且带上数量
    text, param = asyncio.run(build_switch_pm(3, "https://example.com/b", "en-us"))
    assert text and "3" in text
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", param)


# ── 3. /start payload 分支（plugins.start）───────────────────────────────


@asynccontextmanager
async def _fake_session():
    yield None


def _start_msg(*command: str) -> SimpleNamespace:
    return SimpleNamespace(from_user=SimpleNamespace(id=1), command=list(command), reply=AsyncMock())


def test_start_with_valid_token_runs_parse_url():
    msg = _start_msg("start", "tok123")
    with (
        patch.object(start_module, "get_session", _fake_session),
        patch.object(start_module, "UserService") as user_service,
        patch.object(start_module, "parse_url", new=AsyncMock()) as parse_url,
        patch.object(start_module, "inline_start_link") as link,
    ):
        user_service.return_value.get_lang = AsyncMock(return_value="zh-hans")
        link.resolve = AsyncMock(return_value="https://example.com/multi")
        asyncio.run(start_module.start(SimpleNamespace(), msg))

    parse_url.assert_awaited_once()
    assert parse_url.await_args.args[2] == "https://example.com/multi"
    msg.reply.assert_not_awaited()


def test_start_with_unknown_token_replies_expired():
    msg = _start_msg("start", "expired")
    with (
        patch.object(start_module, "get_session", _fake_session),
        patch.object(start_module, "UserService") as user_service,
        patch.object(start_module, "inline_start_link") as link,
    ):
        user_service.return_value.get_lang = AsyncMock(return_value="zh-hans")
        link.resolve = AsyncMock(return_value=None)
        asyncio.run(start_module.start(SimpleNamespace(), msg))

    msg.reply.assert_awaited_once()
    assert "链接已失效" in msg.reply.await_args.args[0]


def test_plain_start_shows_help():
    msg = _start_msg("start")
    with (
        patch.object(start_module, "get_session", _fake_session),
        patch.object(start_module, "UserService") as user_service,
        patch.object(start_module, "build_start_text", return_value={"zh-hans": "HELP"}),
    ):
        user_service.return_value.get_lang = AsyncMock(return_value="zh-hans")
        asyncio.run(start_module.start(SimpleNamespace(), msg))

    msg.reply.assert_awaited_once()
    assert msg.reply.await_args.args[0] == "HELP"
