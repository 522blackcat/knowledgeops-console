"""
身份认证基础能力。

密码：
    Argon2 哈希。

访问令牌：
    HS256 JWT。

JWT 只保存必要身份字段：
    sub、tenant_id、role、iat、exp、type。

不把 JWT 中的 role 直接当作最终权限依据。
后续请求仍从数据库读取当前用户角色和状态。
"""

import uuid

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import jwt

from jwt.exceptions import (
    InvalidTokenError,
)

from pwdlib import PasswordHash

from infrastructure.config import (
    get_settings,
)


password_hasher = (
    PasswordHash.recommended()
)


class AuthenticationError(Exception):
    """访问令牌或身份验证失败。"""


def hash_password(
    plain_password: str,
) -> str:
    """生成 Argon2 密码哈希。"""

    if len(plain_password) < 12:
        raise ValueError(
            "密码至少需要 12 个字符"
        )

    return password_hasher.hash(
        plain_password
    )


def verify_password(
    plain_password: str,
    password_hash: str,
) -> bool:
    """验证密码，不比较明文。"""

    return password_hasher.verify(
        plain_password,
        password_hash,
    )


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
) -> str:
    """签发短期访问令牌。"""

    settings = get_settings()

    if len(settings.session_secret) < 32:
        raise RuntimeError(
            "SESSION_SECRET 未配置或长度不足"
        )

    now = datetime.now(
        timezone.utc
    )

    expires_at = now + timedelta(
        minutes=(
            settings
            .access_token_expire_minutes
        )
    )

    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.session_secret,
        algorithm="HS256",
    )


def decode_access_token(
    token: str,
) -> dict:
    """
    验证 JWT 签名、有效期及必要字段。

    不接受客户端指定算法。
    """

    settings = get_settings()

    if len(settings.session_secret) < 32:
        raise AuthenticationError(
            "服务端认证配置不完整"
        )

    try:
        payload = jwt.decode(
            token,
            settings.session_secret,
            algorithms=["HS256"],
            options={
                "require": [
                    "sub",
                    "tenant_id",
                    "type",
                    "iat",
                    "exp",
                ],
            },
        )

        if payload.get("type") != "access":
            raise AuthenticationError(
                "令牌类型不正确"
            )

        # 提前验证 UUID 格式，避免后续数据库
        # 查询出现未处理的类型转换异常。
        uuid.UUID(
            str(payload["sub"])
        )

        uuid.UUID(
            str(payload["tenant_id"])
        )

        return payload

    except (
        InvalidTokenError,
        ValueError,
        KeyError,
    ) as exc:
        raise AuthenticationError(
            "访问令牌无效或已过期"
        ) from exc
