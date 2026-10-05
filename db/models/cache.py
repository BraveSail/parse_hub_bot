# pylint: disable=not-callable
# (SQLAlchemy 的 func.now() 是运行期动态属性, pylint 静态解析不到 -> 误报 not-callable)
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Cache(Base):
    """**已停用**: 解析缓存换到 Redis 了 (见 ``services/cache.py``)。

    保留这个模型与表是因为 alembic 的历史迁移引用它 —— 删模型会让迁移链断掉。
    表里的旧数据不再被读写 (缓存可再生, 所以没做迁移); 想清干净可以直接删表,
    但要在 alembic 那边一起处理。
    """

    __tablename__ = "cache"
    __table_args__ = (Index("ix_cache_lru", "accessed_at", "updated_at", "created_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    entry_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
