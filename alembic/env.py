"""
Alembic 迁移环境。

迁移使用同步 psycopg 连接。
FastAPI 业务请求使用异步 asyncpg 连接。

两者指向同一个 PostgreSQL 数据库。
"""

from logging.config import fileConfig

from alembic import context

from sqlalchemy import (
    create_engine,
    pool,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import Base

# 必须导入 ORM 模块，才能将所有表注册到 Base.metadata。
import infrastructure.models  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(
        config.config_file_name
    )


target_metadata = Base.metadata


def get_migration_database_url() -> str:
    """
    将 asyncpg URL 转为 psycopg URL。

    Alembic 同步迁移不复用业务 AsyncEngine。
    """

    url = get_settings().database_url

    if url.startswith(
        "postgresql+asyncpg://"
    ):
        return url.replace(
            "postgresql+asyncpg://",
            "postgresql+psycopg://",
            1,
        )

    if url.startswith(
        "postgresql+psycopg://"
    ):
        return url

    raise ValueError(
        "Alembic 仅支持 PostgreSQL，"
        "DATABASE_URL 应使用 "
        "postgresql+asyncpg:// 或 "
        "postgresql+psycopg://"
    )


def run_migrations_offline() -> None:
    """生成 SQL，但不连接数据库。"""

    context.configure(
        url=get_migration_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named"
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连接 PostgreSQL 执行迁移。"""

    engine = create_engine(
        get_migration_database_url(),
        poolclass=pool.NullPool,
    )

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()

    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()

else:
    run_migrations_online()
