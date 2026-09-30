"""
BGE Reranker。

对向量检索与 BM25 融合后的候选 Chunk
进行交叉编码器重排。

默认只从本地缓存加载模型；需要下载时请先运行
scripts/warm_rag_models.py --reranker。
"""

from functools import lru_cache

from sentence_transformers import (
    CrossEncoder,
)

from infrastructure.config import (
    get_settings,
)


@lru_cache(maxsize=1)
def get_reranker():
    """进程内复用重排模型。"""

    settings = get_settings()

    return CrossEncoder(
        settings.reranker_model,
        trust_remote_code=False,
        local_files_only=(
            settings.rag_model_local_files_only
        ),
    )


def rerank(
    *,
    query: str,
    texts: list[str],
) -> list[float]:
    """
    返回每个候选文本的相关性分数。

    返回顺序与输入 texts 一致。
    """

    if not texts:
        return []

    pairs = [
        (query, text)
        for text in texts
    ]

    scores = (
        get_reranker()
        .predict(
            pairs,
            show_progress_bar=False,
        )
    )

    return [
        float(score)
        for score in scores
    ]
