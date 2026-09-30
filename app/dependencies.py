"""
FastAPI 身份、权限和租户依赖。

处理顺序：
    1. 读取 Bearer Token；
    2. 验证 JWT；
    3. 从数据库读取当前用户；
    4. 检查用户是否启用；
    5. 检查 JWT tenant_id 与用户 tenant_id；
    6. 检查租户是否启用；
    7. 根据数据库中的当前角色检查权限。

不允许通过请求参数任意切换 tenant_id。
"""

import uuid

from dataclasses import dataclass

from fastapi import (
    Depends,
    HTTPException,
    status,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.rbac import (
    Permission,
    has_permission,
)

from app.security import (
    AuthenticationError,
    decode_access_token,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    Tenant,
    User,
)


bearer_scheme = HTTPBearer(
    auto_error=False
)


@dataclass(frozen=True)
class CurrentUser:
    """
    当前请求身份。

    业务层只使用服务端确认后的身份，
    不信任客户端提交的 user_id 或 tenant_id。
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    username: str
    role: str


async def get_current_user(
    credentials: (
        HTTPAuthorizationCredentials | None
    ) = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """解析并验证当前登录用户。"""

    unauthorized = HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
        ),
        detail="未登录或登录状态已失效",
        headers={
            "WWW-Authenticate": "Bearer"
        },
    )

    if credentials is None:
        raise unauthorized

    if credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        payload = decode_access_token(
            credentials.credentials
        )

        user_id = uuid.UUID(
            payload["sub"]
        )

        tenant_id = uuid.UUID(
            payload["tenant_id"]
        )

    except (
        AuthenticationError,
        ValueError,
        KeyError,
    ):
        raise unauthorized from None

    user = await db.scalar(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )

    if user is None:
        raise unauthorized

    tenant = await db.scalar(
        select(Tenant).where(
            Tenant.id == tenant_id,
            Tenant.is_active.is_(True),
        )
    )

    if tenant is None:
        raise unauthorized

    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        role=user.role,
    )


def require_permission(
    permission: Permission,
):
    """
    生成权限依赖。

    使用示例：

        @router.get(
            "/runs",
            dependencies=[
                Depends(
                    require_permission(
                        Permission.RUN_READ
                    )
                )
            ],
        )
    """

    async def dependency(
        current_user: CurrentUser = Depends(
            get_current_user
        ),
    ) -> CurrentUser:

        if not has_permission(
            current_user.role,
            permission,
        ):
            raise HTTPException(
                status_code=(
                    status.HTTP_403_FORBIDDEN
                ),
                detail="当前角色没有此操作权限",
            )

        return current_user

    return dependency


def require_same_tenant(
    *,
    resource_tenant_id: uuid.UUID,
    current_user: CurrentUser,
) -> None:
    """
    业务层的显式租户校验。

    对于不属于当前租户的资源，
    返回 404，避免暴露其他租户资源是否存在。
    """

    if (
        resource_tenant_id
        != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail="资源不存在",
        )
