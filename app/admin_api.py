"""管理员接口。"""

from pydantic import (
    BaseModel,
    Field,
)

from datetime import (
    datetime,
)

from uuid import UUID

from sqlalchemy import (
    delete,
    func,
    select,
)

from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from app.security import (
    hash_password,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    AgentDefinition,
    AgentRun,
    ApprovalRequest,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    DocumentChunk,
    IngestBatch,
    IngestJob,
    KnowledgeBase,
    KnowledgeDocument,
    LongTermMemory,
    RunEvent,
    ToolExecution,
    User,
)


router = APIRouter(
    prefix="/api/admin",
    tags=["管理员"],
)


class UserCreateRequest(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=100,
    )
    password: str = Field(
        min_length=12,
    )
    role: str = Field(
        pattern="^(admin|operator|viewer)$",
    )
    is_active: bool = True


class TableUpdateRequest(BaseModel):
    values: dict = Field(
        default_factory=dict
    )


TENANT_TABLES = [
    ("users", User),
    ("agent_definitions", AgentDefinition),
    ("conversations", Conversation),
    ("conversation_messages", ConversationMessage),
    ("conversation_summaries", ConversationSummary),
    ("long_term_memories", LongTermMemory),
    ("agent_runs", AgentRun),
    ("run_events", RunEvent),
    ("approval_requests", ApprovalRequest),
    ("tool_executions", ToolExecution),
    ("knowledge_bases", KnowledgeBase),
    ("knowledge_documents", KnowledgeDocument),
    ("document_chunks", DocumentChunk),
    ("ingest_jobs", IngestJob),
    ("ingest_batches", IngestBatch),
]

TABLE_MAP = {
    name: model
    for name, model in TENANT_TABLES
}

READ_ONLY_TABLES = {
    "run_events",
    "conversation_messages",
    "conversation_summaries",
    "document_chunks",
    "ingest_batches",
}

EDITABLE_FIELDS = {
    "users": {
        "username",
        "role",
        "is_active",
    },
    "agent_definitions": {
        "name",
        "description",
        "status",
        "configuration",
    },
    "conversations": {
        "title",
        "status",
    },
    "agent_runs": {
        "status",
        "error_code",
        "error_message",
    },
    "approval_requests": {
        "status",
        "review_reason",
    },
    "knowledge_bases": {
        "name",
        "description",
    },
    "knowledge_documents": {
        "status",
        "metadata_json",
        "vector_cleanup_status",
    },
    "ingest_jobs": {
        "status",
        "retry_count",
        "last_error",
    },
}


def serialize_value(value):
    """转换 ORM 字段为 JSON 安全值。"""

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    return value


def serialize_row(row) -> dict:
    """转换一条 ORM 记录。"""

    return {
        column.name: serialize_value(
            getattr(row, column.name)
        )
        for column in row.__table__.columns
        if column.name != "password_hash"
    }


def model_for_table(table: str):
    """获取允许管理的表模型。"""

    model = TABLE_MAP.get(table)

    if model is None:
        raise HTTPException(
            status_code=404,
            detail="表不存在或不可管理",
        )

    return model


async def get_tenant_row(
    db: AsyncSession,
    *,
    model,
    row_id: UUID,
    tenant_id,
):
    """读取当前租户的一条记录。"""

    return await db.scalar(
        select(model)
        .where(
            model.id == row_id,
            model.tenant_id == tenant_id,
        )
        .with_for_update()
    )


async def delete_runs(
    db: AsyncSession,
    *,
    tenant_id,
    run_ids: list[UUID],
) -> int:
    """删除运行任务及其子记录。"""

    if not run_ids:
        return 0

    await db.execute(
        delete(RunEvent).where(
            RunEvent.tenant_id == tenant_id,
            RunEvent.run_id.in_(run_ids),
        )
    )
    await db.execute(
        delete(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.run_id.in_(run_ids),
        )
    )
    await db.execute(
        delete(ToolExecution).where(
            ToolExecution.tenant_id == tenant_id,
            ToolExecution.run_id.in_(run_ids),
        )
    )
    result = await db.execute(
        delete(AgentRun).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id.in_(run_ids),
        )
    )
    return int(result.rowcount or 0)


async def delete_conversations(
    db: AsyncSession,
    *,
    tenant_id,
    conversation_ids: list[UUID],
) -> int:
    """删除会话、消息、摘要和对应运行任务。"""

    if not conversation_ids:
        return 0

    run_rows = await db.execute(
        select(AgentRun.id).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.conversation_id.in_(
                conversation_ids
            ),
        )
    )
    await delete_runs(
        db,
        tenant_id=tenant_id,
        run_ids=list(run_rows.scalars().all()),
    )
    await db.execute(
        delete(ConversationMessage).where(
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.conversation_id.in_(
                conversation_ids
            ),
        )
    )
    await db.execute(
        delete(ConversationSummary).where(
            ConversationSummary.tenant_id == tenant_id,
            ConversationSummary.conversation_id.in_(
                conversation_ids
            ),
        )
    )
    result = await db.execute(
        delete(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id.in_(conversation_ids),
        )
    )
    return int(result.rowcount or 0)


async def delete_documents(
    db: AsyncSession,
    *,
    tenant_id,
    document_ids: list[UUID],
) -> int:
    """删除知识库文档及入库任务、批次和分片。"""

    if not document_ids:
        return 0

    job_rows = await db.execute(
        select(IngestJob.id).where(
            IngestJob.tenant_id == tenant_id,
            IngestJob.document_id.in_(document_ids),
        )
    )
    job_ids = list(job_rows.scalars().all())

    if job_ids:
        await db.execute(
            delete(IngestBatch).where(
                IngestBatch.tenant_id == tenant_id,
                IngestBatch.ingest_job_id.in_(
                    job_ids
                ),
            )
        )
        await db.execute(
            delete(IngestJob).where(
                IngestJob.tenant_id == tenant_id,
                IngestJob.id.in_(job_ids),
            )
        )

    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id.in_(
                document_ids
            ),
        )
    )
    result = await db.execute(
        delete(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.id.in_(document_ids),
        )
    )
    return int(result.rowcount or 0)


async def table_count(
    db: AsyncSession,
    model,
    tenant_id,
) -> int:
    """统计当前租户表记录数。"""

    return int(
        await db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.tenant_id == tenant_id
            )
        )
        or 0
    )


async def status_counts(
    db: AsyncSession,
    model,
    tenant_id,
) -> list[dict]:
    """按 status 字段统计队列状态。"""

    rows = await db.execute(
        select(
            model.status,
            func.count(),
        )
        .where(
            model.tenant_id == tenant_id
        )
        .group_by(
            model.status
        )
        .order_by(
            model.status.asc()
        )
    )

    return [
        {
            "status": status,
            "count": int(count),
        }
        for status, count in rows.all()
    ]


@router.get("/overview")
async def admin_overview(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.AUDIT_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """查看当前租户内置表和队列状态。"""

    tables = []

    for name, model in TENANT_TABLES:
        tables.append({
            "name": name,
            "rows": await table_count(
                db,
                model,
                current_user.tenant_id,
            ),
        })

    queues = [
        {
            "name": "agent_runs",
            "description": "Agent 运行任务",
            "statuses": await status_counts(
                db,
                AgentRun,
                current_user.tenant_id,
            ),
        },
        {
            "name": "ingest_jobs",
            "description": "RAG 文档入库任务",
            "statuses": await status_counts(
                db,
                IngestJob,
                current_user.tenant_id,
            ),
        },
        {
            "name": "approval_requests",
            "description": "人工审批队列",
            "statuses": await status_counts(
                db,
                ApprovalRequest,
                current_user.tenant_id,
            ),
        },
    ]

    users = await db.execute(
        select(User)
        .where(
            User.tenant_id
            == current_user.tenant_id
        )
        .order_by(
            User.created_at.desc()
        )
        .limit(100)
    )

    return {
        "tables": tables,
        "queues": queues,
        "users": [
            {
                "id": str(user.id),
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at,
            }
            for user in users.scalars().all()
        ],
    }


@router.get("/tables/{table}/rows")
async def list_table_rows(
    table: str,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.AUDIT_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """查看当前租户指定内置表的最近记录。"""

    model = model_for_table(table)

    result = await db.execute(
        select(model)
        .where(
            model.tenant_id
            == current_user.tenant_id
        )
        .order_by(
            model.created_at.desc()
            if hasattr(model, "created_at")
            else model.id.desc()
        )
        .limit(100)
    )

    return {
        "table": table,
        "read_only": (
            table in READ_ONLY_TABLES
        ),
        "editable_fields": sorted(
            EDITABLE_FIELDS.get(
                table,
                set(),
            )
        ),
        "rows": [
            serialize_row(row)
            for row in result.scalars().all()
        ],
    }


@router.patch("/tables/{table}/rows/{row_id}")
async def update_table_row(
    table: str,
    row_id: UUID,
    body: TableUpdateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.USER_MANAGE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """更新当前租户指定表的一条记录。"""

    if table in READ_ONLY_TABLES:
        raise HTTPException(
            status_code=403,
            detail="该表为只读表",
        )

    allowed_fields = EDITABLE_FIELDS.get(
        table,
        set(),
    )

    invalid_fields = set(
        body.values
    ) - allowed_fields

    if invalid_fields:
        raise HTTPException(
            status_code=422,
            detail=(
                "不允许更新字段："
                + ", ".join(
                    sorted(invalid_fields)
                )
            ),
        )

    model = model_for_table(table)
    row = await get_tenant_row(
        db,
        model=model,
        row_id=row_id,
        tenant_id=current_user.tenant_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="记录不存在",
        )

    for field, value in body.values.items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)

    return serialize_row(row)


@router.delete("/tables/{table}/rows/{row_id}")
async def delete_table_row(
    table: str,
    row_id: UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.USER_MANAGE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """删除当前租户指定表的一条记录。"""

    if table in READ_ONLY_TABLES:
        raise HTTPException(
            status_code=403,
            detail="该表为只读表",
        )

    model = model_for_table(table)
    row = await get_tenant_row(
        db,
        model=model,
        row_id=row_id,
        tenant_id=current_user.tenant_id,
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="记录不存在",
        )

    if (
        table == "users"
        and row.id == current_user.id
    ):
        raise HTTPException(
            status_code=409,
            detail="不能删除当前登录用户",
        )

    try:
        deleted = 1

        if table == "agent_definitions":
            conversation_rows = await db.execute(
                select(Conversation.id).where(
                    Conversation.tenant_id
                    == current_user.tenant_id,
                    Conversation.agent_id == row.id,
                )
            )
            deleted += await delete_conversations(
                db,
                tenant_id=current_user.tenant_id,
                conversation_ids=list(
                    conversation_rows.scalars().all()
                ),
            )
            run_rows = await db.execute(
                select(AgentRun.id).where(
                    AgentRun.tenant_id
                    == current_user.tenant_id,
                    AgentRun.agent_id == row.id,
                )
            )
            deleted += await delete_runs(
                db,
                tenant_id=current_user.tenant_id,
                run_ids=list(
                    run_rows.scalars().all()
                ),
            )

        elif table == "users":
            conversation_rows = await db.execute(
                select(Conversation.id).where(
                    Conversation.tenant_id
                    == current_user.tenant_id,
                    Conversation.user_id == row.id,
                )
            )
            deleted += await delete_conversations(
                db,
                tenant_id=current_user.tenant_id,
                conversation_ids=list(
                    conversation_rows.scalars().all()
                ),
            )
            run_rows = await db.execute(
                select(AgentRun.id).where(
                    AgentRun.tenant_id
                    == current_user.tenant_id,
                    AgentRun.user_id == row.id,
                )
            )
            deleted += await delete_runs(
                db,
                tenant_id=current_user.tenant_id,
                run_ids=list(
                    run_rows.scalars().all()
                ),
            )

        elif table == "conversations":
            deleted = await delete_conversations(
                db,
                tenant_id=current_user.tenant_id,
                conversation_ids=[row.id],
            )
            await db.commit()
            return {
                "table": table,
                "id": str(row_id),
                "status": "deleted",
                "deleted_count": deleted,
            }

        elif table == "agent_runs":
            deleted = await delete_runs(
                db,
                tenant_id=current_user.tenant_id,
                run_ids=[row.id],
            )
            await db.commit()
            return {
                "table": table,
                "id": str(row_id),
                "status": "deleted",
                "deleted_count": deleted,
            }

        elif table == "knowledge_bases":
            document_rows = await db.execute(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.tenant_id
                    == current_user.tenant_id,
                    KnowledgeDocument.knowledge_base_id
                    == row.id,
                )
            )
            deleted += await delete_documents(
                db,
                tenant_id=current_user.tenant_id,
                document_ids=list(
                    document_rows.scalars().all()
                ),
            )

        elif table == "knowledge_documents":
            deleted = await delete_documents(
                db,
                tenant_id=current_user.tenant_id,
                document_ids=[row.id],
            )
            await db.commit()
            return {
                "table": table,
                "id": str(row_id),
                "status": "deleted",
                "deleted_count": deleted,
            }

        elif table == "ingest_jobs":
            await db.execute(
                delete(IngestBatch).where(
                    IngestBatch.tenant_id
                    == current_user.tenant_id,
                    IngestBatch.ingest_job_id
                    == row.id,
                )
            )

        await db.delete(row)
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "该记录被其他数据引用，不能直接删除。"
                "请先删除关联数据，或改为禁用/归档。"
            ),
        ) from exc

    return {
        "table": table,
        "id": str(row_id),
        "status": "deleted",
        "deleted_count": deleted,
    }


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.USER_MANAGE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """创建当前租户的新用户。"""

    user = User(
        tenant_id=current_user.tenant_id,
        username=body.username.strip(),
        password_hash=hash_password(
            body.password
        ),
        role=body.role,
        is_active=body.is_active,
    )

    db.add(user)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        ) from exc

    await db.refresh(user)

    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }
