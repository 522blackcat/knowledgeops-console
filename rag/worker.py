"""
RAG 文档录入 Worker。

Worker 通过 PostgreSQL 行锁领取任务，
Redis 只用于减少空闲轮询。

核心保证：
    - SKIP LOCKED 避免多个 Worker 领取同一任务；
    - 租约过期后允许恢复；
    - Chunk ID 稳定；
    - Qdrant upsert 可重试；
    - 全部批次成功后才发布文档 ready 状态。

本 Worker 是独立进程，不在 FastAPI 内运行。
"""

import asyncio
import hashlib
import os
import socket
import uuid

from datetime import (
    timedelta,
)

from sqlalchemy import (
    delete,
    func,
    or_,
    select,
)

from sqlalchemy.dialects.postgresql import (
    insert,
)

from qdrant_client import models

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import (
    session_scope,
)

from infrastructure.logging import (
    configure_logging,
    get_logger,
)

from infrastructure.models import (
    DocumentChunk,
    IngestBatch,
    IngestJob,
    KnowledgeBase,
    KnowledgeDocument,
    utc_now,
)

from infrastructure.redis_client import (
    get_redis,
)

from rag.chunking import (
    split_sections,
)

from rag.embedding import (
    embed_texts,
)

from rag.ingest_utils import (
    chunk_text_hash,
    iter_chunk_batches,
    stable_chunk_id,
)

from rag.parser import (
    parse_document,
)

from rag.storage import (
    resolve_storage_path,
)

from rag.vector_store import (
    ensure_collection,
    upsert_vectors,
)


settings = get_settings()

logger = get_logger(
    "rag.worker"
)

WORKER_ID = (
    f"{socket.gethostname()}:"
    f"{os.getpid()}:"
    f"{uuid.uuid4().hex[:8]}"
)


async def claim_ingest_job():
    """
    领取一个排队或租约过期的录入任务。

    使用 PostgreSQL SKIP LOCKED，
    支持多个 Worker 并发领取不同任务。
    """

    now = utc_now()

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                or_(
                    IngestJob.status == "queued",
                    (
                        (IngestJob.status == "processing")
                        & (
                            IngestJob.lease_until
                            < now
                        )
                    ),
                )
            )
            .order_by(
                IngestJob.created_at.asc()
            )
            .with_for_update(
                skip_locked=True
            )
            .limit(1)
        )

        if job is None:
            return None

        job.status = "processing"
        job.lease_owner = WORKER_ID
        job.lease_until = (
            now
            + timedelta(
                seconds=(
                    settings.ingest_lease_seconds
                )
            )
        )

        await db.flush()

        return job.id


async def renew_ingest_lease(
    job_id: uuid.UUID,
) -> bool:
    """延长当前 Worker 持有的租约。"""

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
                IngestJob.status == "processing",
            )
            .with_for_update()
        )

        if job is None:
            return False

        job.lease_until = (
            utc_now()
            + timedelta(
                seconds=(
                    settings.ingest_lease_seconds
                )
            )
        )

        return True


async def ingest_lease_heartbeat(
    job_id: uuid.UUID,
    stop_event: asyncio.Event,
) -> None:
    """
    独立延长 RAG 任务租约。

    Embedding 模型加载或单批次推理期间，
    也会定期续租。
    """

    interval = max(
        1,
        settings.ingest_lease_seconds // 3,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=interval,
            )

            return

        except asyncio.TimeoutError:
            pass

        renewed = await renew_ingest_lease(
            job_id
        )

        if not renewed:
            logger.warning(
                "ingest_lease_lost",
                job_id=str(job_id),
            )

            stop_event.set()
            return


async def load_job_context(
    job_id: uuid.UUID,
):
    """读取录入任务、文档和知识库。"""

    async with session_scope() as db:
        result = await db.execute(
            select(
                IngestJob,
                KnowledgeDocument,
                KnowledgeBase,
            )
            .join(
                KnowledgeDocument,
                IngestJob.document_id
                == KnowledgeDocument.id,
            )
            .join(
                KnowledgeBase,
                KnowledgeDocument.knowledge_base_id
                == KnowledgeBase.id,
            )
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
                IngestJob.status == "processing",
                IngestJob.tenant_id
                == KnowledgeDocument.tenant_id,
                KnowledgeDocument.tenant_id
                == KnowledgeBase.tenant_id,
            )
        )

        row = result.one_or_none()

        if row is None:
            raise RuntimeError(
                "任务不存在、租约已失效或租户不匹配"
            )

        job, document, knowledge_base = row

        return {
            "job_id": job.id,
            "tenant_id": job.tenant_id,
            "document_id": document.id,
            "document_version": (
                job.document_version
            ),
            "storage_path": (
                job.storage_path
                or document.storage_path
            ),
            "content_hash": (
                job.content_hash
                or document.content_hash
            ),
            "filename": (
                job.filename
                or document.filename
            ),
            "collection_name": (
                knowledge_base.collection_name
            ),
        }


async def prepare_batches(
    *,
    job_id: uuid.UUID,
    chunks,
) -> None:
    """
    持久化 Chunk 元数据和批次记录。

    使用 PostgreSQL ON CONFLICT，
    恢复任务时不会重复创建记录。
    """

    batch_size = (
        settings.rag_qdrant_upsert_batch_size
    )

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
                IngestJob.status == "processing",
            )
            .with_for_update()
        )

        if job is None:
            raise RuntimeError(
                "录入任务租约已失效"
            )

        job.total_chunks = len(chunks)

        for chunk in chunks:
            chunk_id = stable_chunk_id(
                document_id=job.document_id,
                document_version=(
                    job.document_version
                ),
                chunk_index=(
                    chunk.chunk_index
                ),
            )

            statement = (
                insert(DocumentChunk)
                .values(
                    id=chunk_id,
                    tenant_id=job.tenant_id,
                    document_id=job.document_id,
                    document_version=(
                        job.document_version
                    ),
                    chunk_index=(
                        chunk.chunk_index
                    ),
                    text=chunk.text,
                    text_hash=chunk_text_hash(
                        chunk.text
                    ),
                    source_page=(
                        chunk.source_page
                    ),
                    metadata_json=(
                        chunk.metadata
                    ),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "document_id",
                        "document_version",
                        "chunk_index",
                    ]
                )
            )

            await db.execute(
                statement
            )

        for batch_index, batch in (
            iter_chunk_batches(
                chunks,
                batch_size=batch_size,
            )
        ):
            statement = (
                insert(IngestBatch)
                .values(
                    tenant_id=job.tenant_id,
                    ingest_job_id=job.id,
                    batch_index=batch_index,
                    start_chunk_index=(
                        batch[0].chunk_index
                    ),
                    end_chunk_index=(
                        batch[-1].chunk_index
                    ),
                    status="pending",
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "ingest_job_id",
                        "batch_index",
                    ]
                )
            )

            await db.execute(
                statement
            )


async def get_completed_batches(
    job_id: uuid.UUID,
) -> set[int]:
    """读取已成功写入 Qdrant 的批次编号。"""

    async with session_scope() as db:
        result = await db.execute(
            select(
                IngestBatch.batch_index
            ).where(
                IngestBatch.ingest_job_id
                == job_id,
                IngestBatch.status
                == "completed",
            )
        )

        return set(
            result.scalars().all()
        )


async def mark_batch_completed(
    *,
    job_id: uuid.UUID,
    batch_index: int,
    chunk_count: int,
) -> None:
    """
    在一个事务中完成批次并更新任务进度。

    如果批次已完成，直接返回，
    防止恢复时重复增加 completed_chunks。
    """

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
                IngestJob.status == "processing",
            )
            .with_for_update()
        )

        if job is None:
            raise RuntimeError(
                "录入任务租约已失效"
            )

        batch = await db.scalar(
            select(IngestBatch)
            .where(
                IngestBatch.ingest_job_id
                == job_id,
                IngestBatch.batch_index
                == batch_index,
            )
            .with_for_update()
        )

        if batch is None:
            raise RuntimeError(
                "录入批次不存在"
            )

        if batch.status == "completed":
            return

        batch.status = "completed"
        batch.completed_at = utc_now()

        job.completed_chunks += (
            chunk_count
        )

        job.lease_until = (
            utc_now()
            + timedelta(
                seconds=(
                    settings.ingest_lease_seconds
                )
            )
        )


async def complete_ingest_job(
    job_id: uuid.UUID,
) -> None:
    """
    确认全部批次完成后发布文档 ready 状态。

    文档状态更新与任务完成在同一个事务内。
    """

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
                IngestJob.status == "processing",
            )
            .with_for_update()
        )

        if job is None:
            raise RuntimeError(
                "录入任务租约已失效"
            )

        incomplete_count = await db.scalar(
            select(
                func.count(IngestBatch.id)
            ).where(
                IngestBatch.ingest_job_id
                == job_id,
                IngestBatch.status
                != "completed",
            )
        )

        if incomplete_count:
            raise RuntimeError(
                "仍存在未完成的录入批次"
            )

        if (
            job.completed_chunks
            != job.total_chunks
        ):
            raise RuntimeError(
                "录入进度与 Chunk 总数不一致"
            )

        document = await db.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.id
                == job.document_id,
                KnowledgeDocument.tenant_id
                == job.tenant_id,
            )
            .with_for_update()
        )

        if document is None:
            raise RuntimeError(
                "录入文档不存在"
            )

        if document.deleted_at is not None:
            raise RuntimeError(
                "文档已删除，拒绝发布录入结果"
            )

        if (
            job.document_version
            < document.current_version
        ):
            raise RuntimeError(
                "文档版本已过期，拒绝发布旧版本"
            )

        if (
            job.document_version
            > document.current_version + 1
        ):
            raise RuntimeError(
                "文档版本不连续"
            )

        # 新版本所有批次均已成功写入 Qdrant，
        # 现在才更新 PostgreSQL 的当前版本。
        document.current_version = (
            job.document_version
        )

        document.storage_path = (
            job.storage_path
            or document.storage_path
        )

        document.content_hash = (
            job.content_hash
            or document.content_hash
        )

        document.filename = (
            job.filename
            or document.filename
        )

        document.status = "ready"

        job.status = "completed"
        job.lease_owner = None
        job.lease_until = None
        job.last_error = None


async def fail_ingest_job(
    job_id: uuid.UUID,
    error: Exception,
) -> None:
    """
    记录失败并决定是否重试。

    达到最大重试次数后将任务标记为 failed。
    """

    async with session_scope() as db:
        job = await db.scalar(
            select(IngestJob)
            .where(
                IngestJob.id == job_id,
                IngestJob.lease_owner
                == WORKER_ID,
            )
            .with_for_update()
        )

        if job is None:
            return

        job.retry_count += 1

        job.last_error = (
            str(error)[:2000]
        )

        job.lease_owner = None
        job.lease_until = None

        if job.retry_count > (
            settings.ingest_max_retries
        ):
            job.status = "failed"

            document = await db.scalar(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.id
                    == job.document_id,
                    KnowledgeDocument.tenant_id
                    == job.tenant_id,
                )
                .with_for_update()
            )

            if (
                document is not None
                and document.current_version
                == job.document_version
                and document.status != "ready"
            ):
                document.status = "failed"

        else:
            job.status = "queued"


async def process_ingest_job(
    job_id: uuid.UUID,
) -> None:
    """执行一个完整文档录入任务。"""

    context = await load_job_context(
        job_id
    )

    source = resolve_storage_path(
        context["storage_path"]
    )

    # 检查文件是否在上传后被意外修改。
    digest = hashlib.sha256()

    with source.open("rb") as file:
        while True:
            block = file.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    if digest.hexdigest() != (
        context["content_hash"]
    ):
        raise RuntimeError(
            "文档内容哈希校验失败"
        )

    sections = await asyncio.to_thread(
        parse_document,
        source,
    )

    chunks = split_sections(
        sections,
        chunk_size=(
            settings.rag_chunk_size
        ),
        overlap=(
            settings.rag_chunk_overlap
        ),
    )

    if not chunks:
        raise RuntimeError(
            "文档未解析出可索引文本"
        )

    await ensure_collection(
        context["collection_name"]
    )

    await prepare_batches(
        job_id=job_id,
        chunks=chunks,
    )

    completed_batches = (
        await get_completed_batches(
            job_id
        )
    )

    for batch_index, batch in (
        iter_chunk_batches(
            chunks,
            batch_size=(
                settings
                .rag_qdrant_upsert_batch_size
            ),
        )
    ):
        if batch_index in completed_batches:
            continue

        if not await renew_ingest_lease(
            job_id
        ):
            raise RuntimeError(
                "录入任务租约已失效"
            )

        vectors = await asyncio.to_thread(
            embed_texts,
            [
                chunk.text
                for chunk in batch
            ],
        )

        points = []

        for chunk, vector in zip(
            batch,
            vectors,
            strict=True,
        ):
            chunk_id = stable_chunk_id(
                document_id=(
                    context["document_id"]
                ),
                document_version=(
                    context["document_version"]
                ),
                chunk_index=(
                    chunk.chunk_index
                ),
            )

            points.append(
                models.PointStruct(
                    id=str(chunk_id),
                    vector=vector,
                    payload={
                        "tenant_id": str(
                            context["tenant_id"]
                        ),
                        "document_id": str(
                            context["document_id"]
                        ),
                        "document_version": (
                            context[
                                "document_version"
                            ]
                        ),
                        "chunk_index": (
                            chunk.chunk_index
                        ),
                    },
                )
            )

        await upsert_vectors(
            collection_name=(
                context["collection_name"]
            ),
            points=points,
        )

        await mark_batch_completed(
            job_id=job_id,
            batch_index=batch_index,
            chunk_count=len(batch),
        )

    await complete_ingest_job(
        job_id
    )


async def worker_loop(
    slot_index: int,
) -> None:
    """
    一个独立的 RAG Worker 并发槽位。

    多个槽位使用 PostgreSQL SKIP LOCKED，
    不会同时领取同一个有效租约的任务。
    """

    logger.info(
        "rag_worker_slot_started",
        worker_id=WORKER_ID,
        slot=slot_index,
    )

    while True:
        job_id = await claim_ingest_job()

        if job_id is None:
            await asyncio.sleep(2)
            continue

        stop_event = asyncio.Event()

        heartbeat_task = asyncio.create_task(
            ingest_lease_heartbeat(
                job_id,
                stop_event,
            )
        )

        try:
            await process_ingest_job(
                job_id
            )

            logger.info(
                "ingest_completed",
                job_id=str(job_id),
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.exception(
                "ingest_failed",
                job_id=str(job_id),
            )

            await fail_ingest_job(
                job_id,
                exc,
            )

        finally:
            stop_event.set()

            heartbeat_task.cancel()

            await asyncio.gather(
                heartbeat_task,
                return_exceptions=True,
            )


async def main() -> None:
    """RAG Worker 命令行入口。"""

    configure_logging()

    concurrency = max(
        1,
        settings.ingest_worker_concurrency,
    )

    logger.info(
        "rag_worker_started",
        worker_id=WORKER_ID,
        concurrency=concurrency,
    )

    async with asyncio.TaskGroup() as group:
        for slot_index in range(
            concurrency
        ):
            group.create_task(
                worker_loop(
                    slot_index
                )
            )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
