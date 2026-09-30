"""Warm RAG embedding and reranker models in the container cache."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.embedding import (
    embed_query,
    get_embedding_model,
)

from rag.reranker import (
    get_reranker,
    rerank,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download/load RAG models into the Docker model cache."
    )
    parser.add_argument(
        "--reranker",
        action="store_true",
        help="Also warm the CrossEncoder reranker model.",
    )
    args = parser.parse_args()

    os.environ["RAG_MODEL_LOCAL_FILES_ONLY"] = "false"

    started = time.perf_counter()
    print("warming embedding model...")
    model = get_embedding_model()
    print(
        "embedding dimension:",
        model.get_sentence_embedding_dimension(),
    )
    embed_query("模型预热：Python 参数如何传递？")

    if args.reranker:
        print("warming reranker model...")
        get_reranker()
        rerank(
            query="Python 参数如何传递？",
            texts=[
                "Python 采用对象共享传参。",
                "这是一段无关文本。",
            ],
        )

    elapsed = time.perf_counter() - started
    print(f"RAG models warmed in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
