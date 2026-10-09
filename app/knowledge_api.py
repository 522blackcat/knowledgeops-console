"""
知识库与文档上传 API。

上传流程：
    1. 验证当前租户和权限；
    2. 流式保存文件；
    3. 创建文档和录入任务；
    4. 提交 PostgreSQL；
    5. 通知 RAG Worker。

不在 HTTP 请求中执行模型推理或文档向量化。
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.audit import write_audit_log

from app.rbac import Permission

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    IngestJob,
    KnowledgeBase,
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
    tags=["知识库"],
)


ACTIVE_INGEST_STATUSES = (
    "queued",
    "processing",
)


class KnowledgeBaseCreateRequest(
    BaseModel
):
    name: str = Field(
        min_length=1,
        max_length=150,
    )

    description: str = ""
    scope: str = Field(
        default="general",
        max_length=50,
    )


@router.post(
    "/bases",
    status_code=201,
)
async def create_knowledge_base(
    body: KnowledgeBaseCreateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """创建当前租户的知识库。"""

    settings = get_settings()

    knowledge_base_id = uuid.uuid4()

    collection_name = (
        f"{settings.rag_collection_prefix}_"
        f"{knowledge_base_id.hex}"
    )

    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        scope=body.scope,
        collection_name=collection_name,
    )

    db.add(knowledge_base)

    await write_audit_log(
        db,
        current_user=current_user,
        action="knowledge_base.create",
        resource_type="knowledge_base",
        resource_id=str(knowledge_base.id),
        summary=f"创建知识库：{knowledge_base.name}",
        metadata={
            "name": knowledge_base.name,
            "scope": knowledge_base.scope,
            "collection_name": (
                knowledge_base.collection_name
            ),
        },
    )

    await db.commit()

    return {
        "id": str(knowledge_base.id),
        "name": knowledge_base.name,
        "description": knowledge_base.description,
        "scope": knowledge_base.scope,
        "collection_name": (
            knowledge_base.collection_name
        ),
    }


@router.get(
    "/bases",
)
async def list_knowledge_bases(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户的知识库。"""

    result = await db.execute(
        select(KnowledgeBase)
        .where(
            KnowledgeBase.tenant_id
            == current_user.tenant_id
        )
        .order_by(
            KnowledgeBase.created_at.desc()
        )
        .limit(100)
    )

    return [
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description,
            "scope": item.scope,
        }
        for item in result.scalars().all()
    ]


@router.post(
    "/bases/{knowledge_base_id}/documents",
    status_code=202,
)
async def upload_document(
    knowledge_base_id: uuid.UUID,
    file: UploadFile = File(...),
    external_id: str | None = Form(
        default=None
    ),
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """上传一个文档并创建录入任务。"""

    knowledge_base = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id
            == knowledge_base_id,
            KnowledgeBase.tenant_id
            == current_user.tenant_id,
        )
    )

    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail="知识库不存在",
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

    # external_id 由调用方指定时可用于业务去重；
    # 未指定时生成随机 ID。
    document_external_id = (
        external_id
        or uuid.uuid4().hex
    )

    if len(document_external_id) > 255:
        resolve_storage_path(
            stored.storage_path
        ).unlink(
            missing_ok=True
        )

        raise HTTPException(
            status_code=422,
            detail="external_id 长度超过限制",
        )

    existing = await db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id
            == current_user.tenant_id,
            KnowledgeDocument.knowledge_base_id
            == knowledge_base_id,
            KnowledgeDocument.external_id
            == document_external_id,
        )
    )

    if existing is not None:
        resolve_storage_path(
            stored.storage_path
        ).unlink(
            missing_ok=True
        )

        raise HTTPException(
            status_code=409,
            detail=(
                "external_id 已存在；"
                "文档版本更新接口将在后续提供"
            ),
        )

    duplicate = await db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id
            == current_user.tenant_id,
            KnowledgeDocument.knowledge_base_id
            == knowledge_base_id,
            KnowledgeDocument.content_hash
            == stored.content_hash,
            KnowledgeDocument.deleted_at.is_(None),
        )
    )

    if duplicate is not None:
        resolve_storage_path(
            stored.storage_path
        ).unlink(
            missing_ok=True
        )

        await write_audit_log(
            db,
            current_user=current_user,
            action="document.upload_duplicate",
            resource_type="knowledge_document",
            resource_id=str(duplicate.id),
            summary=(
                f"重复上传文档：{stored.filename}"
            ),
            metadata={
                "knowledge_base_id": str(
                    knowledge_base_id
                ),
                "filename": stored.filename,
                "content_hash": stored.content_hash,
            },
        )
        await db.commit()

        return {
            "document_id": str(
                duplicate.id
            ),
            "ingest_job_id": None,
            "status": "duplicate",
            "message": "相同内容已存在，未重复入库",
        }

    document = KnowledgeDocument(
        tenant_id=current_user.tenant_id,
        knowledge_base_id=knowledge_base_id,
        external_id=document_external_id,
        filename=stored.filename,
        storage_path=stored.storage_path,
        content_hash=stored.content_hash,
        current_version=1,
        status="pending",
    )

    db.add(document)

    await db.flush()

    job = IngestJob(
        tenant_id=current_user.tenant_id,
        document_id=document.id,
        document_version=1,
        storage_path=stored.storage_path,
        content_hash=stored.content_hash,
        filename=stored.filename,
        status="queued",
    )

    db.add(job)

    await write_audit_log(
        db,
        current_user=current_user,
        action="document.upload",
        resource_type="knowledge_document",
        resource_id=str(document.id),
        summary=f"上传文档：{document.filename}",
        metadata={
            "knowledge_base_id": str(
                knowledge_base_id
            ),
            "ingest_job_id": str(job.id),
            "filename": document.filename,
            "content_hash": document.content_hash,
        },
    )

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
        # Worker 定期扫描 PostgreSQL，
        # 因此 Redis 通知丢失不会丢任务。
        pass

    return {
        "document_id": str(document.id),
        "ingest_job_id": str(job.id),
        "status": "queued",
    }


@router.post(
    "/documents/{document_id}/reindex",
    status_code=202,
)
async def reindex_document(
    document_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    用现有文件重建文档索引。

    这里不提前切换 current_version。
    Worker 在新版本全部切片和向量写入成功后，
    才发布新版本，因此重建期间旧版本仍可检索。
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
            IngestJob.status.in_(
                ACTIVE_INGEST_STATUSES
            ),
        )
        .limit(1)
    )

    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "该文档已有正在处理的入库任务，"
                "请等待完成后再重建"
            ),
        )

    if not resolve_storage_path(
        document.storage_path
    ).exists():
        raise HTTPException(
            status_code=409,
            detail="原始文件不存在，无法重建索引",
        )

    max_job_version = await db.scalar(
        select(
            func.max(
                IngestJob.document_version
            )
        ).where(
            IngestJob.document_id
            == document.id,
            IngestJob.tenant_id
            == current_user.tenant_id,
        )
    )

    next_version = (
        max(
            document.current_version,
            max_job_version or 0,
        )
        + 1
    )

    job = IngestJob(
        tenant_id=current_user.tenant_id,
        document_id=document.id,
        document_version=next_version,
        storage_path=document.storage_path,
        content_hash=document.content_hash,
        filename=document.filename,
        status="queued",
    )
    db.add(job)
    await db.flush()

    await write_audit_log(
        db,
        current_user=current_user,
        action="document.reindex",
        resource_type="knowledge_document",
        resource_id=str(document.id),
        summary=(
            f"重建文档索引：{document.filename}"
        ),
        metadata={
            "ingest_job_id": str(job.id),
            "document_version": next_version,
            "current_version": (
                document.current_version
            ),
        },
    )

    await db.commit()

    try:
        await get_redis().publish(
            "rag:ingest:wakeup",
            str(job.id),
        )

    except Exception:
        pass

    return {
        "document_id": str(document.id),
        "ingest_job_id": str(job.id),
        "document_version": next_version,
        "current_version": (
            document.current_version
        ),
        "status": "queued",
    }


@router.get(
    "/jobs",
)
async def list_ingest_jobs(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户最近的文档录入任务。"""

    result = await db.execute(
        select(
            IngestJob,
            KnowledgeDocument,
        )
        .join(
            KnowledgeDocument,
            IngestJob.document_id
            == KnowledgeDocument.id,
        )
        .where(
            IngestJob.tenant_id
            == current_user.tenant_id,
            KnowledgeDocument.tenant_id
            == current_user.tenant_id,
        )
        .order_by(
            IngestJob.created_at.desc()
        )
        .limit(100)
    )

    return [
        {
            "id": str(job.id),
            "document_id": str(
                job.document_id
            ),
            "document_version": (
                job.document_version
            ),
            "current_version": (
                document.current_version
            ),
            "filename": (
                job.filename
                or document.filename
            ),
            "document_status": (
                document.status
            ),
            "status": job.status,
            "completed_chunks": (
                job.completed_chunks
            ),
            "total_chunks": job.total_chunks,
            "retry_count": job.retry_count,
            "last_error": job.last_error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        for job, document in result.all()
    ]


@router.get(
    "/jobs/{job_id}",
)
async def get_ingest_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """查看文档录入进度。"""

    job = await db.scalar(
        select(IngestJob).where(
            IngestJob.id == job_id,
            IngestJob.tenant_id
            == current_user.tenant_id,
        )
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="录入任务不存在",
        )

    return {
        "id": str(job.id),
        "document_id": str(
            job.document_id
        ),
        "status": job.status,
        "completed_chunks": (
            job.completed_chunks
        ),
        "total_chunks": job.total_chunks,
        "retry_count": job.retry_count,
        "last_error": job.last_error,
    }
