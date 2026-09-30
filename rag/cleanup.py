"""
Qdrant 向量清理。

数据库软删除是检索可见性的权威状态。
向量清理失败时，可再次运行本模块。

当前清理函数按文档 ID 删除所有版本的向量。
"""

import uuid

from sqlalchemy import select

from infrastructure.database import (
    session_scope,
)

from infrastructure.logging import (
    get_logger,
)

from infrastructure.models import (
    KnowledgeBase,
    KnowledgeDocument,
    utc_now,
)

from rag.vector_store import (
    delete_document_vectors,
)


logger = get_logger(
    "rag.cleanup"
)


async def cleanup_deleted_document(
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
) -> bool:
    """
    删除一个已软删除文档的向量。

    返回 True：
        清理完成，或文档已经不存在。

    返回 False：
        文档尚未软删除，不允许清理。
    """

    async with session_scope() as db:
        result = await db.execute(
            select(
                KnowledgeDocument,
                KnowledgeBase,
            )
            .join(
                KnowledgeBase,
                KnowledgeDocument.knowledge_base_id
                == KnowledgeBase.id,
            )
            .where(
                KnowledgeDocument.id
                == document_id,
                KnowledgeDocument.tenant_id
                == tenant_id,
                KnowledgeBase.tenant_id
                == tenant_id,
            )
        )

        row = result.one_or_none()

        if row is None:
            return True

        document, knowledge_base = row

        if document.deleted_at is None:
            return False

        collection_name = (
            knowledge_base.collection_name
        )

    # 不在数据库事务中等待 Qdrant。
    await delete_document_vectors(
        collection_name=collection_name,
        tenant_id=tenant_id,
        document_id=document_id,
    )

    async with session_scope() as db:
        document = await db.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.deleted_at.is_not(None),
            )
            .with_for_update()
        )

        if document is not None:
            document.vector_cleanup_status = "completed"
            document.vector_cleanup_at = utc_now()

    logger.info(
        "document_vectors_deleted",
        tenant_id=str(tenant_id),
        document_id=str(document_id),
    )

    return True
