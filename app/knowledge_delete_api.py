"""
文档软删除 API。

先在 PostgreSQL 标记删除，
使检索立即排除该文档。

Qdrant 向量删除属于后续清理步骤；
即使清理失败，也不会重新暴露已删除文档。
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    IngestJob,
    KnowledgeDocument,
    utc_now,
)


router = APIRouter(
    prefix="/api/knowledge",
    tags=["知识库文档"],
)


@router.delete(
    "/documents/{document_id}",
)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """软删除当前租户的文档。"""

    document = await db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id
            == document_id,
            KnowledgeDocument.tenant_id
            == current_user.tenant_id,
        )
        .with_for_update()
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )

    if document.deleted_at is None:
        document.deleted_at = utc_now()
        document.status = "deleted"
        document.vector_cleanup_status = "pending"
        document.vector_cleanup_at = None

    active_jobs = await db.execute(
        select(IngestJob)
        .where(
            IngestJob.document_id
            == document_id,
            IngestJob.tenant_id
            == current_user.tenant_id,
            IngestJob.status.in_([
                "queued",
                "processing",
            ]),
        )
        .with_for_update()
    )

    for job in active_jobs.scalars():
        job.status = "cancelled"
        job.lease_owner = None
        job.lease_until = None

    await db.commit()

    return {
        "document_id": str(
            document_id
        ),
        "status": "deleted",
        "vector_cleanup": "pending",
    }
