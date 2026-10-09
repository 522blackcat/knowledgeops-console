"""Audit logging helpers."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser

from infrastructure.models import AuditLog


async def write_audit_log(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    action: str,
    resource_type: str,
    resource_id: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an audit log row inside the caller's transaction."""

    db.add(
        AuditLog(
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.id,
            actor_username=current_user.username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            metadata_json=metadata or {},
        )
    )
