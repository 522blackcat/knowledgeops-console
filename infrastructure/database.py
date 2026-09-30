"""
PostgreSQL 异步数据库基础设施。

每个请求和 Worker 任务使用独立 Session。

正式建表和变更由 Alembic 管理。
此模块不执行 create_all() 或 drop_all()。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sqlalchemy.orm import DeclarativeBase

from infrastructure.config import (
    get_settings,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的共同基类."""

    pass


settings = get_settings()


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)


SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[
    AsyncSession
]:
    """
    显式事务边界。

    正常结束时提交，异常时回滚。
    """

    async with SessionFactory() as session:
        try:
            yield session

            await session.commit()

        except BaseException:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[
    AsyncSession
]:
    """
    FastAPI 数据库依赖。

    此依赖不自动提交。
    API 应通过 session_scope() 或显式
    commit() 定义写入事务边界。
    """

    async with SessionFactory() as session:
        yield session


async def close_database() -> None:
    """关闭数据库连接池。"""

    await engine.dispose()
