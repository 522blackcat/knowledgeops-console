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
import uuid

from dataclasses import dataclass

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
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
    vector_limit: int = 30,
    bm25_limit: int = 30,
    rerank_limit: int = 12,
    final_limit: int = 6,
    use_reranker: bool = True,
) -> list[RetrievedChunk]:
    """
    当前租户指定知识库的混合检索。

    所有候选 Chunk 最终再次通过 PostgreSQL
    校验 tenant_id、文档状态和文档版本，
    不直接信任 Qdrant payload。
    """

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

    query_vector = await asyncio.to_thread(
        embed_query,
        query,
    )

    vector_hits = await search_vectors(
        collection_name=(
            knowledge_base.collection_name
        ),
        tenant_id=tenant_id,
        query_vector=query_vector,
        limit=vector_limit,
    )

    bm25_hits = await bm25_search(
        db,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=query,
        limit=bm25_limit,
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

    fused = reciprocal_rank_fusion([
        vector_ids,
        bm25_ids,
    ])

    candidate_ids = [
        chunk_id
        for chunk_id, _ in (
            fused[:rerank_limit]
        )
    ]

    if not candidate_ids:
        return []

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

    if not ordered_rows:
        return []

    if not use_reranker:
        fused_scores = dict(
            fused
        )
        return [
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
                    "filename": document.filename,
                    "external_id": document.external_id,
                    **chunk.metadata_json,
                },
            )
            for chunk, document in ordered_rows[
                :final_limit
            ]
        ]

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

    return [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=document.id,
            text=chunk.text,
            source_page=chunk.source_page,
            score=float(score),
            metadata={
                "filename": document.filename,
                "external_id": document.external_id,
                **chunk.metadata_json,
            },
        )
        for (
            (chunk, document),
            score,
        ) in ranked[:final_limit]
    ]
