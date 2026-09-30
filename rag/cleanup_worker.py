"""
软删除文档的向量清理 Worker。

定期扫描已软删除文档。
删除操作必须可重复执行。

为避免引入新的数据库表，本教学版本
使用周期性扫描和 Qdrant 幂等删除。
"""

import asyncio

from sqlalchemy import select

from infrastructure.database import (
    session_scope,
)

from infrastructure.logging import (
    configure_logging,
    get_logger,
)

from infrastructure.models import (
    KnowledgeDocument,
)

from rag.cleanup import (
    cleanup_deleted_document,
)


logger = get_logger(
    "rag.cleanup_worker"
)


async def cleanup_once(
    after_id=None,
) -> tuple[int, object | None]:
    """
    执行一轮清理。

    after_id 是上一批最后一个文档 UUID。
    """

    async with session_scope() as db:
        statement = (
            select(
                KnowledgeDocument.tenant_id,
                KnowledgeDocument.id,
            )
            .where(
                KnowledgeDocument.deleted_at.is_not(None),
                KnowledgeDocument.vector_cleanup_status
                .in_(["pending", "failed"]),
            )
        )

        if after_id is not None:
            statement = statement.where(
                KnowledgeDocument.id > after_id
            )

        result = await db.execute(
            statement
            .order_by(
                KnowledgeDocument.id.asc()
            )
            .limit(100)
        )

        candidates = result.all()

    next_id = (
        candidates[-1].id
        if candidates
        else None
    )

    completed = 0

    for tenant_id, document_id in candidates:
        try:
            ok = (
                await cleanup_deleted_document(
                    tenant_id=tenant_id,
                    document_id=document_id,
                )
            )

            if ok:
                completed += 1

        except Exception:
            logger.exception(
                "vector_cleanup_failed",
                document_id=str(
                    document_id
                ),
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
                    document.vector_cleanup_status = "failed"

    return completed, next_id


async def main() -> None:
    """清理 Worker 命令行入口。"""

    configure_logging()

    logger.info(
        "vector_cleanup_worker_started"
    )

    after_id = None

    while True:
        completed, next_id = await cleanup_once(
            after_id=after_id
        )

        logger.info(
            "vector_cleanup_cycle_finished",
            completed=completed,
        )

        # 到达末尾后重新从头扫描，
        # 以便重试之前失败的清理任务。
        after_id = next_id

        if next_id is None:
            after_id = None
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(
        main()
    )
