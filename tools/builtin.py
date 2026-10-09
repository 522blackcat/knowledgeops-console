"""
内置工具。

RAG 检索属于只读工具，
但仍必须检查租户和知识库授权。

不允许模型通过参数指定 tenant_id。
tenant_id 由服务端运行上下文注入。
"""

import uuid

import httpx

from sqlalchemy import select

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import (
    session_scope,
)

from infrastructure.models import (
    KnowledgeBase,
)

from tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
)


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    tenant_id: uuid.UUID,
    allowed_knowledge_base_ids: list[
        uuid.UUID
    ],
) -> None:
    """注册当前 Agent 可用的内置工具。"""

    settings = get_settings()

    async def search_knowledge(
        arguments: dict,
    ) -> dict:
        query = str(
            arguments.get(
                "query",
                "",
            )
        ).strip()

        raw_knowledge_base_id = (
            arguments.get(
                "knowledge_base_id"
            )
        )

        if not query:
            raise ValueError(
                "query 不能为空"
            )

        knowledge_base_id = uuid.UUID(
            str(
                raw_knowledge_base_id
            )
        )

        if knowledge_base_id not in (
            allowed_knowledge_base_ids
        ):
            raise PermissionError(
                "当前 Agent 未获授权使用该知识库"
            )

        async with session_scope() as db:
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
                    "知识库不存在"
                )

        if settings.rag_retrieval_url:
            try:
                async with httpx.AsyncClient(
                    timeout=settings.qdrant_timeout_seconds
                ) as client:
                    response = await client.post(
                        (
                            settings.rag_retrieval_url.rstrip("/")
                            + "/internal/rag/retrieve"
                        ),
                        json={
                            "tenant_id": str(tenant_id),
                            "knowledge_base_ids": [
                                str(knowledge_base_id)
                            ],
                            "query": query,
                            "use_reranker": True,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()

            except (
                httpx.HTTPError,
                ValueError,
            ) as exc:
                return {
                    "query": query,
                    "results": [],
                    "error": {
                        "type": (
                            exc.__class__.__name__
                        ),
                        "message": (
                            "知识库检索服务暂时不可用，"
                            "请稍后重试或检查 rag-api。"
                        ),
                    },
                }

            results = []
            for hit in payload.get("citations") or []:
                metadata = {
                    "knowledge_base_id": hit.get(
                        "knowledge_base_id"
                    ),
                    "knowledge_base_name": hit.get(
                        "knowledge_base_name"
                    ),
                    "knowledge_base_scope": hit.get(
                        "knowledge_base_scope"
                    ),
                    "filename": hit.get("filename"),
                    "document_version": hit.get(
                        "document_version"
                    ),
                    "current_version": hit.get(
                        "current_version"
                    ),
                    "heading": hit.get("heading"),
                    "sheet": hit.get("sheet"),
                    "section_label": hit.get(
                        "section_label"
                    ),
                }
                results.append({
                    "chunk_id": hit.get("chunk_id"),
                    "document_id": hit.get(
                        "document_id"
                    ),
                    "text": hit.get("preview", ""),
                    "source_page": hit.get(
                        "source_page"
                    ),
                    "score": hit.get("score"),
                    "metadata": metadata,
                })

            return {
                "query": query,
                "results": results,
            }

        from rag.retrieval import (
            hybrid_retrieve,
        )

        async with session_scope() as db:
            hits = await hybrid_retrieve(
                db,
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                query=query,
            )

        return {
            "query": query,
            "results": [
                {
                    "chunk_id": str(hit.chunk_id),
                    "document_id": str(
                        hit.document_id
                    ),
                    "text": hit.text,
                    "source_page": hit.source_page,
                    "score": hit.score,
                    "metadata": hit.metadata,
                }
                for hit in hits
            ],
        }

    registry.register(
        ToolDefinition(
            name="search_knowledge",
            description=(
                "在当前 Agent 已授权的知识库中"
                "进行向量与 BM25 混合检索，"
                "返回可引用的文档片段。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "knowledge_base_id": {
                        "type": "string",
                        "description": (
                            "已授权知识库的 UUID"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "需要检索的问题"
                        ),
                    },
                },
                "required": [
                    "knowledge_base_id",
                    "query",
                ],
                "additionalProperties": False,
            },
            risk=ToolRisk.READ,
            handler=search_knowledge,
        )
    )
