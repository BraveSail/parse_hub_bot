"""管理命令的白名单服务。

白名单 = **配置里的 ∪ DB 里的**:

- ``core/config.py::admin_users`` —— 初始/永久的一层 (改 .env 即生效, 不用改代码);
  也解决"第一个人怎么进来" —— 否则 ``/add`` 谁也调不了。
- ``admin_users`` 表 —— 运行时用 ``/add`` 加的人。
"""

from __future__ import annotations

from core import bs
from db import get_session
from log import logger
from repo.admin_user import AdminUserRepo

logger = logger.bind(name="AdminUser")


class AdminUserService:
    def __init__(self, session):
        self._session = session
        self._repo = AdminUserRepo(session)

    @staticmethod
    def configured_ids() -> set[int]:
        """配置里的白名单 (``.env`` 的 ``ADMIN_USERS``, 逗号分隔)。"""
        return set(bs.admin_user_ids)

    async def is_allowed(self, user_id: int | None) -> bool:
        """能否使用管理命令 (/add /list /purge)。"""
        if not user_id:
            return False
        if user_id in self.configured_ids():
            return True
        return await self._repo.get_by_tg_user_id(user_id) is not None

    async def add(self, user_id: int, *, added_by: int | None = None) -> bool:
        """加入白名单; 返回**是否新增** (本来就在则为 False)。

        配置里已有的用户不写 DB —— 否则 DB 会多出一行"其实是配置来的"记录,
        以后从配置删掉时会以为还在白名单里。
        """
        if user_id in self.configured_ids():
            return False
        row = await self._repo.get_by_tg_user_id(user_id)
        if row is not None:
            return False
        await self._repo.add(user_id, added_by=added_by)
        logger.info(f"白名单新增: user_id={user_id} added_by={added_by}")
        return True

    async def remove(self, user_id: int) -> bool:
        """把用户移出白名单, 返回**是否真的从 DB 删掉了**。

        ⚠️ 配置那层删不掉 (它在 ``.env`` 里) —— 调用方**必须**区分"删了"与
        "他在配置里", 否则会对用户谎称"已移除"而其实还在白名单。
        用 ``in_configured()`` 判断。
        """
        removed = await self._repo.remove(user_id)
        if removed:
            logger.info(f"白名单移除: user_id={user_id}")
        return removed

    @staticmethod
    def in_configured(user_id: int) -> bool:
        """该用户是不是**配置**里的 (那种删不掉, 只能改 .env)。"""
        return user_id in AdminUserService.configured_ids()

    async def list_ids(self) -> list[int]:
        """全部白名单 ID。**与加入顺序无关, 排序输出** (列表稳定可读)。"""
        rows = await self._repo.list_all()
        return sorted({*self.configured_ids(), *(r.telegram_user_id for r in rows)})


async def is_admin_user(user_id: int | None) -> bool:
    """便捷判定 (自开会话) —— 命令入口用。"""
    if not user_id:
        return False
    if user_id in AdminUserService.configured_ids():
        return True
    async with get_session() as session:
        return await AdminUserService(session).is_allowed(user_id)


__all__ = ["AdminUserService", "is_admin_user"]
