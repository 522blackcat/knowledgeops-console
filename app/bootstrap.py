"""
初始化默认租户与管理员。

只在首次部署时运行：

    python -m app.bootstrap

已存在同名租户或管理员时，不重复创建。
"""

import asyncio

from sqlalchemy import select

from app.security import (
    hash_password,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import (
    session_scope,
)

from infrastructure.logging import (
    configure_logging,
    get_logger,
)

from infrastructure.models import (
    Tenant,
    User,
)


logger = get_logger(
    "app.bootstrap"
)


async def bootstrap() -> None:
    """创建初始租户和管理员。"""

    settings = get_settings()

    username = (
        settings.bootstrap_admin_username
    ).strip()

    password = (
        settings.bootstrap_admin_password
    )

    if not username:
        raise ValueError(
            "BOOTSTRAP_ADMIN_USERNAME 不能为空"
        )

    if not password or len(password) < 12:
        raise ValueError(
            "BOOTSTRAP_ADMIN_PASSWORD "
            "至少需要 12 个字符"
        )

    async with session_scope() as db:
        tenant = await db.scalar(
            select(Tenant)
            .where(
                Tenant.name == "default"
            )
            .with_for_update()
        )

        if tenant is None:
            tenant = Tenant(
                name="default",
                is_active=True,
            )

            db.add(tenant)

            await db.flush()

        existing = await db.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.username == username,
            )
        )

        if existing is not None:
            logger.info(
                "bootstrap_admin_already_exists",
                username=username,
            )

            return

        admin = User(
            tenant_id=tenant.id,
            username=username,
            password_hash=hash_password(
                password
            ),
            role="admin",
            is_active=True,
        )

        db.add(admin)

        logger.info(
            "bootstrap_admin_created",
            username=username,
            tenant_id=str(tenant.id),
        )


async def main() -> None:
    configure_logging()

    await bootstrap()


if __name__ == "__main__":
    asyncio.run(main())
