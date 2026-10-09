"""
混合检索。

执行顺序：
    1. BGE-M3 查询向量；
    2. Qdrant Dense Vector Search；
    3. PostgreSQL Chunk BM25；
    4. RRF 融合；
    5. BGE Reranker；
    6. 返回带来源的 Chunk。

RRF 不直接比较 BM25 与向量分数，
而是融合两路检索的排名。
"""

import asyncio
import time
import uuid

from dataclasses import dataclass

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.logging import (
    get_logger,
)

from infrastructure.models import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)

from rag.bm25 import (
    bm25_search,
)

from rag.embedding import (
    embed_query,
)

from rag.reranker import (
    rerank,
)

from rag.vector_store import (
    search_vectors,
)


logger = get_logger(
    "rag.retrieval"
)


@dataclass(frozen=True)
class RetrievedChunk:
    """可供 Agent 引用的检索结果。"""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    text: str
    source_page: int | None
    score: float
    metadata: dict


def reciprocal_rank_fusion(
    rankings: list[list[uuid.UUID]],
    *,
    k: int = 60,
) -> list[tuple[uuid.UUID, float]]:
    """
    Reciprocal Rank Fusion。

    每一路排名贡献：
        1 / (k + rank)

    rank 从 1 开始。
    """

    scores: dict[
        uuid.UUID,
        float,
    ] = {}

    for ranking in rankings:
        for rank, chunk_id in enumerate(
            ranking,
            start=1,
        ):
            scores[chunk_id] = (
                scores.get(
                    chunk_id,
                    0.0,
                )
                + 1.0 / (k + rank)
            )

    return sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )


async def hybrid_retrieve(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    query: str,
    vector_limit: int | None = None,
    bm25_limit: int | None = None,
    rerank_limit: int | None = None,
    final_limit: int | None = None,
    use_reranker: bool = True,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """
    当前租户指定知识库的混合检索。

    所有候选 Chunk 最终再次通过 PostgreSQL
    校验 tenant_id、文档状态和文档版本，
    不直接信任 Qdrant payload。

    漏斗深度与 RRF k 未显式传入时读取配置。
    min_score 仅在启用 Reranker 时按交叉编码器分数生效。
    """

    settings = get_settings()

    if vector_limit is None:
        vector_limit = settings.rag_vector_limit

    if bm25_limit is None:
        bm25_limit = settings.rag_bm25_limit

    if rerank_limit is None:
        rerank_limit = settings.rag_rerank_limit

    if final_limit is None:
        final_limit = settings.rag_final_limit

    if min_score is None:
        min_score = settings.rag_min_score

    total_started = time.perf_counter()
    stage_ms: dict[str, float] = {}

    knowledge_base = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id
            == knowledge_base_id,
            KnowledgeBase.tenant_id
            == tenant_id,
        )
    )

    if knowledge_base is None:
        raise ValueError(
            "知识库不存在或不属于当前租户"
        )

    stage_started = time.perf_counter()

    query_vector = await asyncio.to_thread(
        embed_query,
        query,
    )

    stage_ms["embed"] = round(
        (time.perf_counter() - stage_started) * 1000,
        1,
    )

    stage_started = time.perf_counter()

    vector_hits = await search_vectors(
        collection_name=(
            knowledge_base.collection_name
        ),
        tenant_id=tenant_id,
        query_vector=query_vector,
        limit=vector_limit,
    )

    stage_ms["vector_search"] = round(
        (time.perf_counter() - stage_started) * 1000,
        1,
    )

    stage_started = time.perf_counter()

    bm25_hits = await bm25_search(
        db,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=query,
        limit=bm25_limit,
    )

    stage_ms["bm25_search"] = round(
        (time.perf_counter() - stage_started) * 1000,
        1,
    )

    vector_ids = []

    for hit in vector_hits:
        try:
            vector_ids.append(
                uuid.UUID(str(hit.id))
            )

        except ValueError:
            continue

    bm25_ids = [
        hit.chunk_id
        for hit in bm25_hits
    ]

    stage_started = time.perf_counter()

    fused = reciprocal_rank_fusion(
        [
            vector_ids,
            bm25_ids,
        ],
        k=settings.rag_rrf_k,
    )

    candidate_ids = [
        chunk_id
        for chunk_id, _ in (
            fused[:rerank_limit]
        )
    ]

    stage_ms["fusion"] = round(
        (time.perf_counter() - stage_started) * 1000,
        1,
    )

    ordered_rows: list[
        tuple[DocumentChunk, KnowledgeDocument]
    ] = []

    stage_started = time.perf_counter()

    if candidate_ids:
        result = await db.execute(
            select(
                DocumentChunk,
                KnowledgeDocument,
            )
            .join(
                KnowledgeDocument,
                DocumentChunk.document_id
                == KnowledgeDocument.id,
            )
            .where(
                DocumentChunk.id.in_(
                    candidate_ids
                ),
                DocumentChunk.tenant_id
                == tenant_id,
                KnowledgeDocument.tenant_id
                == tenant_id,
                KnowledgeDocument.knowledge_base_id
                == knowledge_base_id,
                KnowledgeDocument.status == "ready",
                KnowledgeDocument.deleted_at.is_(None),
                DocumentChunk.document_version
                == KnowledgeDocument.current_version,
            )
        )

        rows = result.all()

        row_map = {
            chunk.id: (
                chunk,
                document,
            )
            for chunk, document in rows
        }

        ordered_rows = [
            row_map[chunk_id]
            for chunk_id in candidate_ids
            if chunk_id in row_map
        ]

    stage_ms["revalidate"] = round(
        (time.perf_counter() - stage_started) * 1000,
        1,
    )

    dropped_by_revalidation = (
        len(candidate_ids) - len(ordered_rows)
    )

    results: list[RetrievedChunk] = []

    if ordered_rows and use_reranker:
        stage_started = time.perf_counter()

        scores = await asyncio.to_thread(
            rerank,
            query=query,
            texts=[
                chunk.text
                for chunk, _ in ordered_rows
            ],
        )

        ranked = sorted(
            zip(
                ordered_rows,
                scores,
            ),
            key=lambda item: item[1],
            reverse=True,
        )

        if min_score is not None:
            ranked = [
                item
                for item in ranked
                if float(item[1]) >= min_score
            ]

        results = [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                text=chunk.text,
                source_page=chunk.source_page,
                score=float(score),
                metadata={
                    "knowledge_base_id": str(
                        knowledge_base.id
                    ),
                    "knowledge_base_name": (
                        knowledge_base.name
                    ),
                    "knowledge_base_scope": (
                        knowledge_base.scope
                    ),
                    "filename": document.filename,
                    "external_id": document.external_id,
                    "document_version": (
                        chunk.document_version
                    ),
                    "current_version": (
                        document.current_version
                    ),
                    **chunk.metadata_json,
                },
            )
            for (
                (chunk, document),
                score,
            ) in ranked[:final_limit]
        ]

        stage_ms["rerank"] = round(
            (time.perf_counter() - stage_started) * 1000,
            1,
        )

    elif ordered_rows:
        fused_scores = dict(
            fused
        )
        results = [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                text=chunk.text,
                source_page=chunk.source_page,
                score=float(
                    fused_scores.get(
                        chunk.id,
                        0.0,
                    )
                ),
                metadata={
                    "knowledge_base_id": str(
                        knowledge_base.id
                    ),
                    "knowledge_base_name": (
                        knowledge_base.name
                    ),
                    "knowledge_base_scope": (
                        knowledge_base.scope
                    ),
                    "filename": document.filename,
                    "external_id": document.external_id,
                    "document_version": (
                        chunk.document_version
                    ),
                    "current_version": (
                        document.current_version
                    ),
                    **chunk.metadata_json,
                },
            )
            for chunk, document in ordered_rows[
                :final_limit
            ]
        ]

    total_ms = round(
        (time.perf_counter() - total_started) * 1000,
        1,
    )

    for result in results:
        result.metadata["retrieval_stage_ms"] = stage_ms
        result.metadata["retrieval_total_ms"] = total_ms
        result.metadata["vector_hits"] = len(vector_ids)
        result.metadata["bm25_hits"] = len(bm25_ids)
        result.metadata["fused_hits"] = len(fused)
        result.metadata["candidate_hits"] = len(candidate_ids)
        result.metadata["dropped_by_revalidation"] = (
            dropped_by_revalidation
        )

    log_fields = {
        "tenant_id": str(tenant_id),
        "knowledge_base_id": str(
            knowledge_base_id
        ),
        "query_chars": len(query),
        "vector_hits": len(vector_ids),
        "bm25_hits": len(bm25_ids),
        "fused": len(fused),
        "candidates": len(candidate_ids),
        "dropped_by_revalidation": (
            dropped_by_revalidation
        ),
        "returned": len(results),
        "used_reranker": use_reranker,
        "stage_ms": stage_ms,
        "total_ms": total_ms,
    }

    if results:
        logger.info(
            "rag_retrieval_finished",
            **log_fields,
        )
    else:
        logger.warning(
            "rag_retrieval_empty",
            **log_fields,
        )

    return results
