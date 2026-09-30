"""RAG 检索自测 API。"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
)

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)

from rag.bm25 import (
    bm25_search,
)

from rag.retrieval import (
    hybrid_retrieve,
)


router = APIRouter(
    prefix="/api/rag",
    tags=["RAG 自测"],
)


class RagEvalRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=2000,
    )
    knowledge_scope: str = "global"
    knowledge_base_ids: list[uuid.UUID] = Field(
        default_factory=list
    )
    retrieval_mode: str = "lexical"
    use_reranker: bool = False


@router.post("/eval")
async def evaluate_rag(
    body: RagEvalRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """直接返回检索命中，用于验证知识库是否可用。"""

    if body.knowledge_scope == "custom":
        knowledge_base_ids = body.knowledge_base_ids
    else:
        result = await db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.tenant_id
                == current_user.tenant_id
            )
        )
        knowledge_base_ids = list(
            result.scalars().all()
        )

    results = []

    for knowledge_base_id in knowledge_base_ids:
        knowledge_base = await db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.tenant_id
                == current_user.tenant_id,
            )
        )

        if knowledge_base is None:
            continue

        if body.retrieval_mode == "lexical":
            bm25_hits = await bm25_search(
                db,
                tenant_id=current_user.tenant_id,
                knowledge_base_id=knowledge_base.id,
                query=body.query,
                limit=10,
            )
            chunk_ids = [
                hit.chunk_id
                for hit in bm25_hits
            ]
            chunk_scores = {
                hit.chunk_id: hit.score
                for hit in bm25_hits
            }
            if not chunk_ids:
                hits = []
            else:
                rows = await db.execute(
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
                        DocumentChunk.id.in_(chunk_ids),
                        DocumentChunk.tenant_id
                        == current_user.tenant_id,
                        KnowledgeDocument.tenant_id
                        == current_user.tenant_id,
                        KnowledgeDocument.knowledge_base_id
                        == knowledge_base.id,
                        KnowledgeDocument.status == "ready",
                        KnowledgeDocument.deleted_at.is_(None),
                        DocumentChunk.document_version
                        == KnowledgeDocument.current_version,
                    )
                )
                row_map = {
                    chunk.id: (
                        chunk,
                        document,
                    )
                    for chunk, document in rows.all()
                }
                hits = [
                    {
                        "chunk_id": chunk.id,
                        "document_id": document.id,
                        "text": chunk.text,
                        "source_page": chunk.source_page,
                        "score": chunk_scores.get(
                            chunk.id,
                            0.0,
                        ),
                        "metadata": {
                            "filename": document.filename,
                            "external_id": document.external_id,
                            **chunk.metadata_json,
                        },
                    }
                    for chunk_id in chunk_ids
                    if chunk_id in row_map
                    for chunk, document in [
                        row_map[chunk_id]
                    ]
                ]
        else:
            hits = await hybrid_retrieve(
                db,
                tenant_id=current_user.tenant_id,
                knowledge_base_id=knowledge_base.id,
                query=body.query,
                use_reranker=body.use_reranker,
            )

        for hit in hits[:5]:
            if isinstance(hit, dict):
                hit_data = hit
                text = str(
                    hit_data.get("text") or ""
                ).strip()
                metadata = hit_data.get("metadata") or {}
            else:
                hit_data = {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "source_page": hit.source_page,
                    "score": hit.score,
                }
                text = str(hit.text or "").strip()
                metadata = hit.metadata
            results.append({
                "knowledge_base_id": str(
                    knowledge_base.id
                ),
                "knowledge_base_name": (
                    knowledge_base.name
                ),
                "chunk_id": str(hit_data["chunk_id"]),
                "document_id": str(hit_data["document_id"]),
                "filename": metadata.get(
                    "filename",
                    "知识库文档",
                ),
                "source_page": hit_data["source_page"],
                "score": hit_data["score"],
                "text": text,
                "preview": (
                    text[:260] + "..."
                    if len(text) > 260
                    else text
                ),
            })

    results.sort(
        key=lambda item: float(
            item.get("score") or 0
        ),
        reverse=True,
    )

    return {
        "query": body.query,
        "retrieval_mode": body.retrieval_mode,
        "use_reranker": body.use_reranker,
        "hit_count": len(results),
        "results": results[:10],
    }
