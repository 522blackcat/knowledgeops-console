"""
Qdrant 向量索引。

使用 BGE-M3 Dense Embedding。

每个知识库拥有独立 Collection。
每个 Point 的 payload 仍保存 tenant_id，
检索时必须同时过滤 tenant_id 和文档状态。

PostgreSQL 是 Chunk 元数据的权威来源。
"""

import uuid

from functools import lru_cache

from qdrant_client import (
    AsyncQdrantClient,
    models,
)

from qdrant_client.http.exceptions import (
    UnexpectedResponse,
)

from infrastructure.config import (
    get_settings,
)

from rag.embedding import (
    embedding_dimension,
)


@lru_cache(maxsize=1)
def get_qdrant_client() -> AsyncQdrantClient:
    """创建可复用的异步 Qdrant 客户端。"""

    settings = get_settings()

    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key
            or None
        ),
        timeout=60,
    )


async def close_qdrant() -> None:
    """关闭 Qdrant 客户端。"""

    await get_qdrant_client().close()

    get_qdrant_client.cache_clear()


async def ensure_collection(
    collection_name: str,
) -> None:
    """不存在时创建知识库 Collection。"""

    client = get_qdrant_client()

    exists = await client.collection_exists(
        collection_name
    )

    if exists:
        return

    try:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=embedding_dimension(),
                distance=models.Distance.COSINE,
            ),
        )
    except UnexpectedResponse as exc:
        if exc.status_code != 409:
            raise

    try:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name="tenant_id",
            field_schema=(
                models.PayloadSchemaType.KEYWORD
            ),
        )
    except UnexpectedResponse as exc:
        if exc.status_code != 409:
            raise

    try:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name="document_id",
            field_schema=(
                models.PayloadSchemaType.KEYWORD
            ),
        )
    except UnexpectedResponse as exc:
        if exc.status_code != 409:
            raise


async def upsert_vectors(
    *,
    collection_name: str,
    points: list[models.PointStruct],
) -> None:
    """按稳定 Point ID 批量写入向量。"""

    if not points:
        return

    await get_qdrant_client().upsert(
        collection_name=collection_name,
        points=points,
        wait=True,
    )


async def search_vectors(
    *,
    collection_name: str,
    tenant_id: uuid.UUID,
    query_vector: list[float],
    limit: int = 20,
):
    """
    在当前租户的 Collection 中执行向量搜索。

    返回 Qdrant ScoredPoint 列表。
    """

    response = await (
        get_qdrant_client()
        .query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(
                            value=str(tenant_id)
                        ),
                    ),
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
    )

    return response.points


async def delete_document_vectors(
    *,
    collection_name: str,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    """删除指定租户和文档的向量。"""

    await get_qdrant_client().delete(
        collection_name=collection_name,
        points_selector=(
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="tenant_id",
                            match=(
                                models.MatchValue(
                                    value=str(tenant_id)
                                )
                            ),
                        ),
                        models.FieldCondition(
                            key="document_id",
                            match=(
                                models.MatchValue(
                                    value=str(document_id)
                                )
                            ),
                        ),
                    ]
                )
            )
        ),
        wait=True,
    )
