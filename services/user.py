from sqlalchemy.ext.asyncio import AsyncSession

from core import bs
from db.models.user import User
from i18n import ISO639_MAP
from repo.user import UserRepo


def locale_for(language_code: str | None, default: str | None = None) -> str:
    """把 Telegram 上报的语言码归一成项目 locale。

    Telegram 用 ISO 639-1 (``ja`` / ``zh-hant``) 且**大小写不定** (``zh-Hant``)，
    这里统一小写后再查表; 查不到就退回 ``default``(默认 bot 全局语言)。
    """
    fallback = default or bs.language
    if not language_code:
        return fallback
    return ISO639_MAP.get(language_code.strip().lower(), fallback)


class UserNotFoundError(Exception):
    pass


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.user = UserRepo(session)

    async def add(self, telegram_user_id: int) -> User:
        return await self.user.add(telegram_user_id)

    async def get(self, telegram_user_id: int) -> User | None:
        return await self.user.get_by_tg_user_id(telegram_user_id)

    async def get_or_raise(self, telegram_user_id: int) -> User:
        if not (user := await self.get(telegram_user_id)):
            raise UserNotFoundError(f"在数据库中找不到用户: {telegram_user_id}")
        return user

    async def ensure(self, telegram_user_id: int) -> User:
        if not (user := await self.get(telegram_user_id)):
            return await self.add(telegram_user_id)
        return user

    async def ensure_lang(self, telegram_user_id: int, telegram_language_code: str | None = None) -> str:
        """取用户语言; 用户**首次出现**时按其 Telegram 语言初始化。

        以前只有"建表默认值 + 手动 /lang"两个来源, Telegram 上报的语言从没被用上,
        于是所有新用户一律是 bot 全局语言 (简中) —— 表现为"日语/繁体用户收到的还是简中"。
        已有用户不会被覆盖 (手动 /lang 的选择要保留)。
        """
        if user := await self.get(telegram_user_id):
            return user.language_code
        locale = locale_for(telegram_language_code)
        await self.set_lang(telegram_user_id, locale)
        return locale

    async def get_lang(self, telegram_user_id: int) -> str:
        if user := await self.get(telegram_user_id):
            return user.language_code
        return bs.language

    async def set_lang(self, telegram_user_id: int, language_code: str) -> User:
        user = await self.ensure(telegram_user_id)
        user.language_code = language_code
        return user
