"""
知识库文档版本管理 API。

更新文档时，旧版本保持 ready，
直到新版本录入成功。

新版本的文件路径与内容哈希保存在
IngestJob 对应的版本记录中。
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
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
)

from infrastructure.redis_client import (
    get_redis,
)

from rag.storage import (
    resolve_storage_path,
    save_upload,
)


router = APIRouter(
    prefix="/api/knowledge",
    tags=["知识库版本"],
)


@router.post(
    "/documents/{document_id}/versions",
    status_code=202,
)
async def upload_document_version(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    为现有文档创建下一版本录入任务。

    不立即修改文档 current_version。
    """

    document = await db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id
            == document_id,
            KnowledgeDocument.tenant_id
            == current_user.tenant_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .with_for_update()
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )

    active_job = await db.scalar(
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
        .limit(1)
    )

    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "该文档已有正在录入的版本，"
                "请等待完成后再更新"
            ),
        )

    try:
        stored = await save_upload(
            file,
            tenant_id=current_user.tenant_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    new_version = (
        document.current_version + 1
    )

    # 本项目现有 IngestJob 模型尚无
    # storage_path/content_hash 字段。
    # 在第 69 节统一为其增加版本文件信息，
    # Worker 将优先读取任务级文件信息。
    job = IngestJob(
        tenant_id=current_user.tenant_id,
        document_id=document.id,
        document_version=new_version,
        storage_path=stored.storage_path,
        content_hash=stored.content_hash,
        filename=stored.filename,
        status="queued",
    )

    db.add(job)

    try:
        await db.commit()

    except BaseException:
        await db.rollback()

        resolve_storage_path(
            stored.storage_path
        ).unlink(
            missing_ok=True
        )

        raise

    try:
        await get_redis().publish(
            "rag:ingest:wakeup",
            str(job.id),
        )

    except Exception:
        pass

    return {
        "document_id": str(
            document.id
        ),
        "ingest_job_id": str(
            job.id
        ),
        "document_version": new_version,
        "status": "queued",
        "current_version": (
            document.current_version
        ),
    }
