"""Audit log API."""

from fastapi import APIRouter, Depends, Query

from sqlalchemy import desc, select

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from infrastructure.database import get_db

from infrastructure.models import AuditLog


router = APIRouter(
    prefix="/api/audit",
    tags=["审计日志"],
)


@router.get("/logs")
async def list_audit_logs(
    action: str | None = Query(
        default=None,
        max_length=80,
    ),
    resource_type: str | None = Query(
        default=None,
        max_length=80,
    ),
    actor: str | None = Query(
        default=None,
        max_length=100,
    ),
    limit: int = Query(
        default=200,
        ge=1,
        le=500,
    ),
    current_user: CurrentUser = Depends(
        require_permission(Permission.AUDIT_READ)
    ),
    db: AsyncSession = Depends(get_db),
):
    """读取当前租户最近的审计日志。"""

    conditions = [
        AuditLog.tenant_id == current_user.tenant_id
    ]

    if action:
        conditions.append(
            AuditLog.action == action
        )

    if resource_type:
        conditions.append(
            AuditLog.resource_type == resource_type
        )

    if actor:
        conditions.append(
            AuditLog.actor_username.ilike(
                f"%{actor}%"
            )
        )

    result = await db.execute(
        select(AuditLog)
        .where(*conditions)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )

    return [
        {
            "id": str(item.id),
            "actor_user_id": (
                str(item.actor_user_id)
                if item.actor_user_id
                else None
            ),
            "actor_username": item.actor_username,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "summary": item.summary,
            "metadata_json": item.metadata_json,
            "created_at": item.created_at,
        }
        for item in result.scalars().all()
    ]
