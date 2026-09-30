"""
基于角色的访问控制（RBAC）。

admin：
    租户内管理权限。

operator：
    创建 Agent 任务、管理知识库、
    审批授权范围内的工具操作。

viewer：
    只读查看被授权的数据。

角色仅在当前 tenant_id 范围内有效。
"""

from enum import StrEnum


class Permission(StrEnum):
    """项目权限枚举。"""

    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"

    RUN_READ = "run:read"
    RUN_CREATE = "run:create"
    RUN_CANCEL = "run:cancel"

    APPROVAL_READ = "approval:read"
    APPROVAL_REVIEW = "approval:review"

    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"

    USER_READ = "user:read"
    USER_MANAGE = "user:manage"

    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[
    str,
    frozenset[Permission],
] = {
    "admin": frozenset(
        Permission
    ),

    "operator": frozenset({
        Permission.AGENT_READ,
        Permission.AGENT_WRITE,
        Permission.RUN_READ,
        Permission.RUN_CREATE,
        Permission.RUN_CANCEL,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_REVIEW,
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_WRITE,
    }),

    "viewer": frozenset({
        Permission.AGENT_READ,
        Permission.RUN_READ,
        Permission.KNOWLEDGE_READ,
    }),
}


def has_permission(
    role: str,
    permission: Permission,
) -> bool:
    """判断角色是否具有指定权限。"""

    return permission in (
        ROLE_PERMISSIONS.get(
            role,
            frozenset(),
        )
    )
