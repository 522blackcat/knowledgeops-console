"""
BGE-M3 文本向量化。

使用 sentence-transformers 加载真实模型。

默认只从本地缓存加载模型，避免 Worker 运行时
因为 HuggingFace 网络探测阻塞入库。
如需下载模型，请先运行 scripts/warm_rag_models.py。
"""

from functools import lru_cache

import numpy as np

from sentence_transformers import (
    SentenceTransformer,
)

from infrastructure.config import (
    get_settings,
)


@lru_cache(maxsize=1)
def get_embedding_model():
    """进程内复用 Embedding 模型。"""

    settings = get_settings()

    return SentenceTransformer(
        settings.embedding_model,
        trust_remote_code=False,
        local_files_only=(
            settings.rag_model_local_files_only
        ),
    )


def embedding_dimension() -> int:
    """读取当前模型的向量维度。"""

    dimension = (
        get_embedding_model()
        .get_sentence_embedding_dimension()
    )

    if dimension is None:
        raise RuntimeError(
            "无法确定 Embedding 维度"
        )

    return int(dimension)


def embed_texts(
    texts: list[str],
) -> list[list[float]]:
    """
    批量生成归一化向量。

    这是同步 CPU/GPU 密集型操作。
    在异步 Worker 中应使用 asyncio.to_thread()
    或独立推理进程调用。
    """

    if not texts:
        return []

    settings = get_settings()

    vectors = (
        get_embedding_model()
        .encode(
            texts,
            batch_size=(
                settings
                .rag_embedding_batch_size
            ),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    )

    array = np.asarray(
        vectors,
        dtype=np.float32,
    )

    if array.ndim != 2:
        raise RuntimeError(
            "Embedding 返回的向量维度异常"
        )

    return array.tolist()


def embed_query(
    query: str,
) -> list[float]:
    """生成单条查询向量。"""

    if not query.strip():
        raise ValueError(
            "查询文本不能为空"
        )

    return embed_texts(
        [query]
    )[0]
