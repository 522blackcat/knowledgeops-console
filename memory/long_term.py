"""
长期记忆 PostgreSQL 主记录。

当前模块实现：
    - 写入；
    - 按用户读取；
    - 软删除；
    - TTL 过滤。

向量化和语义召回将在后续 RAG 模块中接入。
不把普通 SQL 最近记录查询称为向量检索。
"""

import hashlib
import uuid

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from sqlalchemy import (
    or_,
    select,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.models import (
    LongTermMemory,
    utc_now,
)


def memory_content_hash(
    content: str,
) -> str:
    """计算记忆内容的 SHA-256。"""

    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


async def save_long_term_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    memory_type: str,
    conversation_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    ttl_days: int | None = None,
) -> LongTermMemory:
    """
    保存一条长期记忆。

    调用方负责提交事务。
    """

    normalized_content = (
        content.strip()
    )

    if not normalized_content:
        raise ValueError(
            "长期记忆内容不能为空"
        )

    settings = get_settings()

    effective_ttl = (
        settings.memory_default_ttl_days
        if ttl_days is None
        else ttl_days
    )

    if effective_ttl < 0:
        raise ValueError(
            "ttl_days 不能为负数"
        )

    expires_at = None

    if effective_ttl > 0:
        expires_at = (
            utc_now()
            + timedelta(
                days=effective_ttl
            )
        )

    memory = LongTermMemory(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        memory_type=memory_type,
        content=normalized_content,
        content_hash=memory_content_hash(
            normalized_content
        ),
        metadata_json=(
            metadata or {}
        ),
        expires_at=expires_at,
    )

    db.add(memory)

    await db.flush()

    return memory


async def list_long_term_memories(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 50,
) -> list[LongTermMemory]:
    """
    读取当前用户未删除、未过期的长期记忆。

    此接口是数据库读取，不是语义搜索。
    """

    now = datetime.now(
        timezone.utc
    )

    result = await db.execute(
        select(LongTermMemory)
        .where(
            LongTermMemory.tenant_id
            == tenant_id,
            LongTermMemory.user_id
            == user_id,
            LongTermMemory.deleted_at.is_(None),
            or_(
                LongTermMemory.expires_at.is_(None),
                LongTermMemory.expires_at > now,
            ),
        )
        .order_by(
            LongTermMemory.created_at.desc()
        )
        .limit(limit)
    )

    return result.scalars().all()


async def delete_long_term_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
) -> bool:
    """
    软删除一条属于当前用户的长期记忆。

    后续向量索引清理应由独立任务完成。
    """

    memory = await db.scalar(
        select(LongTermMemory)
        .where(
            LongTermMemory.id == memory_id,
            LongTermMemory.tenant_id
            == tenant_id,
            LongTermMemory.user_id
            == user_id,
            LongTermMemory.deleted_at.is_(None),
        )
        .with_for_update()
    )

    if memory is None:
        return False

    memory.deleted_at = utc_now()

    await db.flush()

    return True
