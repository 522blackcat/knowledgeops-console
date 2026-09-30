"""登录与当前用户接口。"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    get_current_user,
)

from app.schemas import (
    LoginRequest,
    LoginResponse,
)

from app.security import (
    create_access_token,
    verify_password,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import User


router = APIRouter(
    prefix="/api/auth",
    tags=["身份认证"],
)


@router.post(
    "/login",
    response_model=LoginResponse,
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    使用用户名和密码登录。

    当前版本用户名必须在所有租户中唯一，
    否则需要在登录请求中增加租户标识。

    为避免错误地登录到其他租户，
    当用户名匹配多个租户时直接拒绝登录。
    """

    result = await db.execute(
        select(User).where(
            User.username == body.username,
            User.is_active.is_(True),
        ).limit(2)
    )

    users = result.scalars().all()

    if len(users) != 1:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="用户名或密码错误",
        )

    user = users[0]

    if not verify_password(
        body.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="用户名或密码错误",
        )

    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
    )

    return LoginResponse(
        access_token=token,
    )


@router.get("/me")
async def get_me(
    current_user: CurrentUser = Depends(
        get_current_user
    ),
):
    """返回服务端确认的当前身份。"""

    return {
        "id": str(current_user.id),
        "tenant_id": str(
            current_user.tenant_id
        ),
        "username": current_user.username,
        "role": current_user.role,
    }
