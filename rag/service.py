"""Internal RAG retrieval service.

This service owns heavyweight embedding/reranker dependencies.
Agent workers call it over the Compose network so they do not need
to carry torch and sentence-transformers in their own image.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from pydantic import BaseModel, Field

from infrastructure.database import (
    close_database,
    session_scope,
)

from infrastructure.logging import (
    configure_logging,
)

from rag.retrieval import hybrid_retrieve


class RagRetrieveRequest(BaseModel):
    tenant_id: uuid.UUID
    knowledge_base_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=50,
    )
    query: str = Field(
        min_length=1,
        max_length=4000,
    )
    use_reranker: bool = True


app = FastAPI(
    title="KnowledgeOps RAG Service",
    version="1.0.0",
)


@app.on_event("startup")
async def on_startup() -> None:
    configure_logging()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await close_database()


def citation_section_label(metadata: dict) -> str | None:
    """Create a readable location label from chunk metadata."""

    heading = metadata.get("heading")
    if heading:
        return str(heading)

    sheet = metadata.get("sheet")
    if sheet:
        return f"sheet:{sheet}"

    return None


def citation_location_label(
    section_label: str | None,
    source_page: int | None,
) -> str:
    if section_label:
        return section_label
    if source_page is not None:
        return f"第 {source_page} 页"
    return "定位未记录"


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "ok"}


@app.post("/internal/rag/retrieve")
async def retrieve(
    body: RagRetrieveRequest,
) -> dict:
    citations: list[dict] = []
    retrieval_stats = {
        "stage_ms": {},
    }

    async with session_scope() as db:
        for knowledge_base_id in body.knowledge_base_ids:
            hits = await hybrid_retrieve(
                db,
                tenant_id=body.tenant_id,
                knowledge_base_id=knowledge_base_id,
                query=body.query,
                use_reranker=body.use_reranker,
            )

            if hits:
                metadata = hits[0].metadata
                stage_ms = (
                    metadata.get("retrieval_stage_ms")
                    or {}
                )
                for name, value in stage_ms.items():
                    retrieval_stats["stage_ms"][name] = round(
                        retrieval_stats["stage_ms"].get(name, 0)
                        + float(value or 0),
                        1,
                    )

                for key in [
                    "retrieval_total_ms",
                    "vector_hits",
                    "bm25_hits",
                    "fused_hits",
                    "candidate_hits",
                    "dropped_by_revalidation",
                ]:
                    retrieval_stats[key] = round(
                        retrieval_stats.get(key, 0)
                        + float(metadata.get(key, 0) or 0),
                        1,
                    )

            for hit in hits:
                text = str(hit.text or "").strip()
                section_label = citation_section_label(
                    hit.metadata
                )
                citations.append({
                    "chunk_id": str(hit.chunk_id),
                    "document_id": str(hit.document_id),
                    "knowledge_base_id": hit.metadata.get(
                        "knowledge_base_id"
                    ),
                    "knowledge_base_name": hit.metadata.get(
                        "knowledge_base_name"
                    ),
                    "knowledge_base_scope": hit.metadata.get(
                        "knowledge_base_scope"
                    ),
                    "filename": hit.metadata.get(
                        "filename",
                        "知识库文档",
                    ),
                    "document_version": hit.metadata.get(
                        "document_version"
                    ),
                    "current_version": hit.metadata.get(
                        "current_version"
                    ),
                    "section_label": section_label,
                    "location_label": citation_location_label(
                        section_label,
                        hit.source_page,
                    ),
                    "heading": hit.metadata.get("heading"),
                    "sheet": hit.metadata.get("sheet"),
                    "source_page": hit.source_page,
                    "score": hit.score,
                    "preview": (
                        text[:220] + "..."
                        if len(text) > 220
                        else text
                    ),
                })

    citations.sort(
        key=lambda item: float(
            item.get("score") or 0
        ),
        reverse=True,
    )

    return {
        "citations": citations,
        "retrieval_stats": retrieval_stats,
    }
