"""管理命令白名单的仓储。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.admin_user import AdminUser


class AdminUserRepo:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_tg_user_id(self, telegram_user_id: int) -> AdminUser | None:
        return await self._session.scalar(
            select(AdminUser).where(AdminUser.telegram_user_id == telegram_user_id)
        )

    async def add(self, telegram_user_id: int, *, added_by: int | None = None) -> AdminUser:
        """加入白名单。**已存在时直接返回既有行** (幂等) ——

        调用方靠返回值判断"新加了"还是"本来就有", 所以这里不能重复插入
        (unique 约束会抛, 而且 /add 重复调用不该报错)。
        """
        if existing := await self.get_by_tg_user_id(telegram_user_id):
            return existing
        row = AdminUser(telegram_user_id=telegram_user_id, added_by=added_by)
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_all(self) -> list[AdminUser]:
        """全部白名单行, 按加入时间排序 (列表输出稳定可读)。"""
        result = await self._session.scalars(select(AdminUser).order_by(AdminUser.created_at, AdminUser.id))
        return list(result)
