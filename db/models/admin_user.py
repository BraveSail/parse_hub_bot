"""管理命令白名单。

**运行时加的人存这里**（初始白名单在 ``core/config.py::admin_users``，两处取并集）。

为什么单独一张表、而不是给 ``users`` 加一列: ``users`` 表是"用户偏好"(语言等),
授权是另一回事 —— 混在一起以后清用户数据会误伤授权。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    #: 谁加的 (审计用; 配置里那批没有这个值)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
