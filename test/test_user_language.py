"""用户语言: 首次出现时跟随 Telegram 上报的语言。

以前只有"建表默认值 + 手动 /lang"两个来源, Telegram 的 language_code 从没被用上 ——
日语/繁体用户收到的仍是 bot 全局语言（简中）。这里钉住映射与"只在首次设置"的契约。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.user import UserService, locale_for

# ── 映射 ────────────────────────────────────────────────────────────────

def test_maps_telegram_codes():
    assert locale_for("ja") == "ja-jp"
    assert locale_for("en") == "en-us"
    assert locale_for("ko") == "ko-kr"
    assert locale_for("zh") == "zh-hans"


def test_traditional_chinese_is_its_own_locale():
    """Telegram 报 zh-hant —— 它跟 zh 是两种语言, 不能套用简中"""
    assert locale_for("zh-hant") == "zh-hant"
    assert locale_for("zh-Hant") == "zh-hant"  # 大小写不定
    assert locale_for("zh-hans") == "zh-hans"


def test_case_and_whitespace_are_normalised():
    assert locale_for("  JA  ") == "ja-jp"
    assert locale_for("ZH-hant") == "zh-hant"


def test_unknown_or_missing_falls_back_to_bot_language():
    from core import bs

    assert locale_for(None) == bs.language
    assert locale_for("") == bs.language
    assert locale_for("xx") == bs.language


def test_explicit_default_is_honoured():
    assert locale_for(None, "en-us") == "en-us"
    assert locale_for("xx", "ja-jp") == "ja-jp"


# ── ensure_lang ─────────────────────────────────────────────────────────

def _service(existing=None) -> tuple[UserService, SimpleNamespace]:
    """造一个只依赖 repo 的 UserService; existing=None 表示这是新用户。"""
    created = SimpleNamespace(language_code=None)
    repo = SimpleNamespace(
        get_by_tg_user_id=AsyncMock(return_value=existing),
        add=AsyncMock(return_value=created),
    )
    service = UserService(session=SimpleNamespace())
    service.user = repo
    return service, created


def test_new_user_gets_telegram_language():
    service, created = _service()
    lang = asyncio.run(service.ensure_lang(123, "ja"))
    assert lang == "ja-jp"
    assert created.language_code == "ja-jp"


def test_new_user_with_traditional_chinese():
    service, created = _service()
    assert asyncio.run(service.ensure_lang(123, "zh-hant")) == "zh-hant"
    assert created.language_code == "zh-hant"


def test_new_user_without_language_gets_bot_default():
    from core import bs

    service, created = _service()
    assert asyncio.run(service.ensure_lang(123, None)) == bs.language


def test_existing_user_is_never_overwritten():
    """手动 /lang 的选择必须保留 —— 已有用户不做任何改动"""
    existing = SimpleNamespace(language_code="zh-hant")
    service, created = _service(existing)
    assert asyncio.run(service.ensure_lang(123, "ja")) == "zh-hant"
    service.user.add.assert_not_awaited()
    assert created.language_code is None
