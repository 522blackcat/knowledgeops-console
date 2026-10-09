"""
项目统一 ORM 模型。

设计约定
--------
1. 业务表显式保存 tenant_id，查询必须按租户过滤。
2. 主键优先使用 UUID，便于分布式任务生成稳定 ID。
3. PostgreSQL 保存业务事实，Redis 只负责协调与通知。
4. Qdrant 保存向量，PostgreSQL 保存文档及 Chunk 元数据。
5. 运行事件使用 RunEvent，不另设 AgentRunEvent。
6. 人工审批使用 ApprovalRequest，不另设 AgentApproval。
7. AgentRun 和 IngestJob 均支持 Worker 租约。
8. 正式数据库结构由 Alembic 迁移管理。

注意
----
tenant_id 字段本身不等于租户隔离。

API、Worker、工具执行和检索服务仍必须在查询、
更新及删除时显式校验 tenant_id。
"""

import uuid

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from sqlalchemy.dialects.postgresql import UUID

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from infrastructure.database import Base


# ========================================================
# 01. 通用工具
# ========================================================

def utc_now() -> datetime:
    """生成带 UTC 时区的当前时间。"""

    return datetime.now(
        timezone.utc
    )


def new_uuid() -> uuid.UUID:
    """生成随机 UUID。"""

    return uuid.uuid4()


# ========================================================
# 02. 租户与用户
# ========================================================

class Tenant(Base):
    """租户，即独立的业务空间。"""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class User(Base):
    """
    本地用户。

    只保存密码哈希，不保存明文密码。

    role 的预期值：
        admin / operator / viewer

    具体权限由后续 RBAC 模块定义。
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    username: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        default="viewer",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "username",
            name="uq_user_tenant_username",
        ),
    )


class AuditLog(Base):
    """租户内关键操作审计日志。"""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    actor_user_id: Mapped[uuid.UUID | None] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id"),
            nullable=True,
            index=True,
        )
    )

    actor_username: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    resource_id: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_audit_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_audit_tenant_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
        ),
    )


# ========================================================
# 03. Agent 定义与会话
# ========================================================

class AgentDefinition(Base):
    """
    管理后台中的 Agent 定义。

    configuration 保存 Agent 的业务配置，
    例如系统提示词、允许使用的工具及知识库 ID。
    """

    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    # idle / running / paused / disabled
    status: Mapped[str] = mapped_column(
        String(30),
        default="idle",
        nullable=False,
    )

    # supervisor / research / tool / general
    agent_type: Mapped[str] = mapped_column(
        String(40),
        default="general",
        nullable=False,
    )

    configuration: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_agent_tenant_status",
            "tenant_id",
            "status",
        ),
    )


class Conversation(Base):
    """
    会话元数据。

    消息和摘要独立存储，
    不把完整历史塞进单个 JSON 字段。
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_definitions.id"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        default="新会话",
        nullable=False,
    )

    # active / archived / deleted
    status: Mapped[str] = mapped_column(
        String(30),
        default="active",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_conversation_tenant_user",
            "tenant_id",
            "user_id",
            "created_at",
        ),
    )


class ConversationMessage(Base):
    """
    原始会话消息。

    sequence_no 在同一会话内单调递增。

    后续记忆模块必须通过事务或数据库锁
    分配 sequence_no，不能简单依赖
    max(sequence_no) + 1 的无锁计算。
    """

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        nullable=False,
    )

    sequence_no: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    # system / user / assistant / tool
    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    tool_call_id: Mapped[
        str | None
    ] = mapped_column(
        String(150),
        nullable=True,
    )

    tool_name: Mapped[
        str | None
    ] = mapped_column(
        String(150),
        nullable=True,
    )

    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_conversation_message_sequence",
        ),
        Index(
            "ix_message_tenant_conversation_sequence",
            "tenant_id",
            "conversation_id",
            "sequence_no",
        ),
    )


# ========================================================
# 04. 增量摘要与长期记忆
# ========================================================

class ConversationSummary(Base):
    """
    增量摘要。

    covered_until_sequence：
        本版本摘要覆盖到的最后一条消息。

    previous_summary_id：
        上一个摘要版本的 ID。

    后续摘要生成逻辑必须使用：
        上一个摘要 + 尚未覆盖的新消息。

    不能每次重新摘要全部历史，
    也不能在新摘要成功持久化前删除旧摘要。
    """

    __tablename__ = "conversation_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    previous_summary_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation_summaries.id"),
        nullable=True,
    )

    covered_until_sequence: Mapped[int] = (
        mapped_column(
            BigInteger,
            nullable=False,
        )
    )

    summary_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "version",
            name="uq_conversation_summary_version",
        ),
        Index(
            "ix_summary_tenant_conversation_version",
            "tenant_id",
            "conversation_id",
            "version",
        ),
    )


class LongTermMemory(Base):
    """
    长期记忆主记录。

    PostgreSQL 保存内容、归属和版本。
    向量索引只负责召回，不作为业务真相来源。
    """

    __tablename__ = "long_term_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    conversation_id: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    memory_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    expires_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    deleted_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_memory_tenant_user_active",
            "tenant_id",
            "user_id",
            "deleted_at",
        ),
        Index(
            "ix_memory_tenant_expiry",
            "tenant_id",
            "expires_at",
        ),
    )


# ========================================================
# 05. Agent 运行任务
# ========================================================

class AgentRun(Base):
    """
    Agent 运行任务。

    status：
        pending
        queued
        running
        waiting_approval
        completed
        failed
        cancelled
        timed_out

    checkpoint_thread_id：
        LangGraph PostgreSQL Checkpoint 的 thread_id。

    lease_owner / lease_until：
        Worker 领取任务时写入。
        租约过期后允许恢复任务。

    注意：
        租约只能降低重复执行概率。
        外部有副作用工具仍必须具备独立幂等机制。
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_definitions.id"),
        nullable=False,
    )

    conversation_id: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("conversations.id"),
            nullable=False,
        )
    )

    status: Mapped[str] = mapped_column(
        String(40),
        default="pending",
        nullable=False,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    answer: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    error_code: Mapped[
        str | None
    ] = mapped_column(
        String(100),
        nullable=True,
    )

    error_message: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    checkpoint_thread_id: Mapped[str] = (
        mapped_column(
            String(150),
            nullable=False,
        )
    )

    idempotency_key: Mapped[
        str | None
    ] = mapped_column(
        String(150),
        nullable=True,
    )

    lease_owner: Mapped[
        str | None
    ] = mapped_column(
        String(150),
        nullable=True,
    )

    lease_until: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    started_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    finished_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_run_tenant_idempotency",
        ),
        Index(
            "ix_run_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_run_status_lease",
            "status",
            "lease_until",
        ),
    )


# ========================================================
# 06. SSE 持久化事件
# ========================================================

class RunEvent(Base):
    """
    Agent 运行事件。

    id 使用数据库自增整数。

    SSE 可直接将 id 作为事件 ID，
    浏览器重连时使用 Last-Event-ID 继续读取。
    """

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    payload_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_run_event_tenant_run_id",
            "tenant_id",
            "run_id",
            "id",
        ),
    )


# ========================================================
# 07. 人工审批
# ========================================================

class ApprovalRequest(Base):
    """
    有副作用工具执行前的人工审批。

    status：
        pending / approved / rejected / expired

    tool_call_id：
        对应模型生成的工具调用 ID。

    arguments_hash：
        对工具名称和规范化参数计算 SHA-256。

    恢复执行前必须重新核对：
        tenant_id、run_id、tool_call_id、
        tool_name 和 arguments_hash。

    不能仅凭前端提交的 approval_id
    就执行任意工具参数。
    """

    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id"),
        nullable=False,
    )

    tool_call_id: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    tool_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    arguments_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    arguments_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # pending / approved / rejected / expired
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
    )

    requested_by: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            nullable=False,
        )
    )

    reviewed_by: Mapped[
        uuid.UUID | None
    ] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    review_reason: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    reviewed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def approval_wait_ms(self) -> int | None:
        """Milliseconds from request creation to review completion."""

        if self.reviewed_at is None:
            return None

        return int(
            (
                self.reviewed_at
                - self.created_at
            ).total_seconds()
            * 1000
        )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_approval_run_tool_call",
        ),
        Index(
            "ix_approval_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
    )


# ========================================================
# 08. 工具执行幂等记录
# ========================================================

class ToolExecution(Base):
    """
    工具执行记录。

    status：
        pending / succeeded / failed

    同一租户内 idempotency_key 唯一。

    对于无法天然幂等的外部操作，
    后续执行器必须将此键传递给外部服务，
    或采用可恢复的业务补偿机制。
    """

    __tablename__ = "tool_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id"),
        nullable=False,
    )

    tool_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    idempotency_key: Mapped[str] = (
        mapped_column(
            String(150),
            nullable=False,
        )
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
    )

    result_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_tool_tenant_idempotency",
        ),
    )


# ========================================================
# 09. RAG 知识库
# ========================================================

class KnowledgeBase(Base):
    """租户拥有的知识库。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )

    scope: Mapped[str] = mapped_column(
        String(50),
        default="general",
        nullable=False,
    )

    collection_name: Mapped[str] = (
        mapped_column(
            String(150),
            nullable=False,
        )
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_kb_tenant_name",
        ),
        UniqueConstraint(
            "collection_name",
            name="uq_kb_collection_name",
        ),
    )


class KnowledgeDocument(Base):
    """
    文档主记录。

    current_version 用于增量更新。

    文档删除采用软删除，
    后续 Worker 再清理对应向量。
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    knowledge_base_id: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("knowledge_bases.id"),
            nullable=False,
        )
    )

    external_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    current_version: Mapped[int] = (
        mapped_column(
            Integer,
            default=1,
            nullable=False,
        )
    )

    # pending / indexing / ready / failed / deleting
    status: Mapped[str] = mapped_column(
        String(40),
        default="pending",
        nullable=False,
    )

    metadata_json: Mapped[dict] = (
        mapped_column(
            JSON,
            default=dict,
            nullable=False,
        )
    )

    vector_cleanup_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    vector_cleanup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    deleted_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "external_id",
            name="uq_kb_document_external_id",
        ),
        Index(
            "ix_document_tenant_kb_status",
            "tenant_id",
            "knowledge_base_id",
            "status",
        ),
    )


class DocumentChunk(Base):
    """
    文档 Chunk 元数据。

    向量本体保存在 Qdrant。
    Chunk ID 用作 Qdrant 的稳定 Point ID。
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("knowledge_documents.id"),
            nullable=False,
        )
    )

    document_version: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
        )
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    text_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    source_page: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    metadata_json: Mapped[dict] = (
        mapped_column(
            JSON,
            default=dict,
            nullable=False,
        )
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "document_version",
            "chunk_index",
            name="uq_document_version_chunk",
        ),
        Index(
            "ix_chunk_tenant_document_version",
            "tenant_id",
            "document_id",
            "document_version",
        ),
    )


# ========================================================
# 10. RAG 录入任务
# ========================================================

class IngestJob(Base):
    """
    一个文档版本对应一个录入任务。

    status：
        queued / processing /
        completed / failed / cancelled

    lease_owner 和 lease_until：
        用于多 Worker 协调与崩溃恢复。
    """

    __tablename__ = "ingest_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    document_id: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("knowledge_documents.id"),
            nullable=False,
        )
    )

    document_version: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
        )
    )

    storage_path: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(40),
        default="queued",
        nullable=False,
    )

    completed_chunks: Mapped[int] = (
        mapped_column(
            BigInteger,
            default=0,
            nullable=False,
        )
    )

    total_chunks: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    lease_owner: Mapped[
        str | None
    ] = mapped_column(
        String(150),
        nullable=True,
    )

    lease_until: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_error: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "document_version",
            name="uq_ingest_document_version",
        ),
        Index(
            "ix_ingest_status_lease",
            "status",
            "lease_until",
        ),
    )


class IngestBatch(Base):
    """
    文档录入批次。

    Worker 恢复时可以跳过 completed 批次。

    批次写入必须使用稳定 Chunk ID，
    防止重试产生重复向量。
    """

    __tablename__ = "ingest_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    ingest_job_id: Mapped[uuid.UUID] = (
        mapped_column(
            UUID(as_uuid=True),
            ForeignKey("ingest_jobs.id"),
            nullable=False,
        )
    )

    batch_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    start_chunk_index: Mapped[int] = (
        mapped_column(
            BigInteger,
            nullable=False,
        )
    )

    end_chunk_index: Mapped[int] = (
        mapped_column(
            BigInteger,
            nullable=False,
        )
    )

    # pending / processing / completed / failed
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        nullable=False,
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    completed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "ingest_job_id",
            "batch_index",
            name="uq_ingest_job_batch",
        ),
    )
