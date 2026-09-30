"""
文档录入辅助函数。

稳定 Chunk ID：
    document_id + document_version + chunk_index

同一个文档版本重试时生成相同 UUID，
Qdrant upsert 会覆盖同一 Point，
不会不断创建重复向量。
"""

import hashlib
import uuid

from rag.chunking import (
    TextChunk,
)


CHUNK_NAMESPACE = uuid.UUID(
    "3f7792aa-8877-4eb3-9b39-1b0d66b51a10"
)


def stable_chunk_id(
    *,
    document_id: uuid.UUID,
    document_version: int,
    chunk_index: int,
) -> uuid.UUID:
    """生成可重复计算的 Chunk UUID。"""

    key = (
        f"{document_id}:"
        f"{document_version}:"
        f"{chunk_index}"
    )

    return uuid.uuid5(
        CHUNK_NAMESPACE,
        key,
    )


def chunk_text_hash(
    text: str,
) -> str:
    """计算 Chunk 文本哈希。"""

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def iter_chunk_batches(
    chunks: list[TextChunk],
    *,
    batch_size: int,
):
    """按固定大小分批遍历 Chunk。"""

    if batch_size <= 0:
        raise ValueError(
            "batch_size 必须大于零"
        )

    for start in range(
        0,
        len(chunks),
        batch_size,
    ):
        yield (
            start // batch_size,
            chunks[
                start:start + batch_size
            ],
        )
