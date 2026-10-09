"""
RAG 检索指标：hit@k / recall@k / MRR。

现有 rag_eval_runner.py 只输出 pass_rate（断言式判定），
无法回答"这次改动让检索变好还是变坏"。本文件补足指标口径，
并且不依赖 API、PostgreSQL 与 Qdrant 服务：
语料直接从目录解析入库到内存，检索走项目真实的分词与 RRF 函数。

相关块标签由用例的期望词自动派生，派生结果写入 labels_audit.json，
必须人工过一遍再采信指标。

用法：
    python -m evaluation.rag_metrics --corpus-dir <目录> --cases evaluation/rag_eval_cases.json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rag.bm25 import score_rows
from rag.chunking import split_sections
from rag.parser import parse_document
from rag.retrieval import reciprocal_rank_fusion


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    filename: str
    metadata: dict
    source_page: int | None = None


def load_corpus(
    corpus_dir: Path,
    chunk_size: int,
    overlap: int,
    files: list[str] | None = None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    if files:
        paths = [corpus_dir / name for name in files]
    else:
        paths = sorted(corpus_dir.iterdir())
    for path in paths:
        if not path.is_file():
            continue
        try:
            sections = parse_document(path)
        except ValueError:
            continue
        for chunk in split_sections(
            sections,
            chunk_size=chunk_size,
            overlap=overlap,
        ):
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}:{chunk.chunk_index}")),
                    text=chunk.text,
                    filename=path.name,
                    metadata=chunk.metadata or {},
                    source_page=chunk.source_page,
                )
            )
    return chunks


def case_terms(case: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("expected_terms", "expected_any_terms", "expected_top_any_terms"):
        for term in case.get(key) or []:
            term = str(term).strip()
            if term and term not in terms:
                terms.append(term)
    return terms


RESULTS_DIR = Path(__file__).with_name("results")


def label_cases(
    cases: list[dict[str, Any]],
    chunks: list[Chunk],
    audit_path: Path | None = None,
) -> dict[str, list[str]]:
    """
    相关块标签。

    优先用 anchor_text 定位：包含该片段的块即相关块。
    锚点与分块无关，所以改 chunk_size 或改分块规则后，
    同一套标签仍然可比。

    锚点整段找不到时退回前缀，仍找不到就标 unlabeled。
    块被切断导致锚点消失属于真实缺陷，
    必须通过 unlabeled 数量暴露出来，不做静默兜底。
    """
    labels: dict[str, list[str]] = {}
    audit: dict[str, dict[str, Any]] = {}
    for case in cases:
        anchor = str(case.get("anchor_text") or "").strip()
        if anchor:
            used = anchor
            relevant = [
                chunk.chunk_id
                for chunk in chunks
                if used in chunk.text
            ]
            if not relevant:
                used = anchor[:24]
                relevant = [
                    chunk.chunk_id
                    for chunk in chunks
                    if used in chunk.text
                ]
            basis = "anchor" if relevant else "anchor_missing"
            terms = [used]
        else:
            terms = case_terms(case)
            relevant = [
                chunk.chunk_id
                for chunk in chunks
                if any(term in chunk.text for term in terms)
            ]
            basis = "terms"
        labels[case["id"]] = relevant
        audit[case["id"]] = {
            "query": case["query"],
            "label_basis": basis,
            "terms": terms,
            "relevant_chunks": [
                {
                    "chunk_id": chunk_id,
                    "filename": next(
                        c.filename for c in chunks if c.chunk_id == chunk_id
                    ),
                    "source_page": next(
                        c.source_page for c in chunks if c.chunk_id == chunk_id
                    ),
                    "preview": next(
                        c.text for c in chunks if c.chunk_id == chunk_id
                    )[:80],
                }
                for chunk_id in relevant
            ],
        }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (audit_path or RESULTS_DIR / "labels_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return labels


def bm25_rank(query: str, chunks: list[Chunk], limit: int) -> list[str]:
    rows = [
        (chunk.chunk_id, chunk.text, chunk.metadata)
        for chunk in chunks
    ]
    return [
        str(hit.chunk_id)
        for hit in score_rows(rows, query, limit)
    ]


def cosine_rank(
    query_vector: list[float],
    chunk_vectors: dict[str, list[float]],
    limit: int,
) -> list[str]:
    """归一化向量的点积即余弦，与 Qdrant 的分数口径一致。"""
    scores = [
        (chunk_id, float(np.dot(query_vector, vector)))
        for chunk_id, vector in chunk_vectors.items()
    ]
    scores.sort(key=lambda item: -item[1])
    return [chunk_id for chunk_id, score in scores[:limit] if score > 0]


def rrf_rank(
    query: str,
    chunks: list[Chunk],
    limit: int,
    lexical_limit: int,
    vector_rank: list[str] | None,
    k: int,
) -> list[str]:
    """与生产一致：只按名次融合，不比分数。"""
    lists = [bm25_rank(query, chunks, lexical_limit)]
    if vector_rank is not None:
        lists.append(vector_rank[:lexical_limit])
    fused = reciprocal_rank_fusion(lists, k=k)
    return [str(chunk_id) for chunk_id, _ in fused[:limit]]


def metrics(
    ranked: list[str],
    relevant: list[str],
    ks: tuple[int, ...],
) -> dict[str, float]:
    relevant_set = set(relevant)
    out: dict[str, float] = {}
    for k in ks:
        top = ranked[:k]
        out[f"hit@{k}"] = float(any(c in relevant_set for c in top))
        out[f"recall@{k}"] = (
            len(relevant_set & set(top)) / len(relevant_set) if relevant_set else 0.0
        )
    reciprocal_rank = 0.0
    for position, chunk_id in enumerate(ranked, start=1):
        if chunk_id in relevant_set:
            reciprocal_rank = 1.0 / position
            break
    out["mrr"] = reciprocal_rank
    out["empty"] = float(not ranked)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--files", nargs="*", default=None,
                        help="只取目录里的这几个文件做语料")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=500,
                        help="分块预算，单位 Token（按 BGE-M3 tokenizer）")
    parser.add_argument("--overlap", type=int, default=50,
                        help="重叠预算，单位 Token")
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--lexical-limit", type=int, default=30)
    parser.add_argument("--return-limit", type=int, default=30)
    parser.add_argument("--ks", default="1,3,5")
    parser.add_argument("--mode", choices=["lexical", "hybrid"], default="lexical")
    parser.add_argument("--label-overrides", type=Path, default=None,
                        help="人工修订后的 labels JSON，覆盖自动派生标签")
    args = parser.parse_args()

    ks = tuple(int(item) for item in args.ks.split(","))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    chunks = load_corpus(args.corpus_dir, args.chunk_size, args.overlap, args.files)
    if not chunks:
        print("语料为空，检查 --corpus-dir", file=sys.stderr)
        return 2

    chunk_vectors: dict[str, list[float]] = {}
    embed_query_fn = None
    if args.mode == "hybrid":
        from rag.embedding import embed_query as embed_query_fn, embed_texts

        chunk_vectors = {
            chunk.chunk_id: vector
            for chunk, vector in zip(
                chunks,
                embed_texts([chunk.text for chunk in chunks]),
            )
        }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.label_overrides:
        labels = json.loads(args.label_overrides.read_text(encoding="utf-8"))
    else:
        labels = label_cases(
            cases,
            chunks,
            audit_path=RESULTS_DIR / f"labels_audit_{args.mode}_{timestamp}.json",
        )

    per_case: list[dict[str, Any]] = []
    by_id = {chunk.chunk_id: chunk for chunk in chunks}

    def cite(chunk_id: str) -> dict[str, Any]:
        """报表里带上引用出处，否则 hit@k 只能看排名不能核对来源。"""
        chunk = by_id[chunk_id]
        return {
            "filename": chunk.filename,
            "source_page": chunk.source_page,
            "preview": chunk.text[:120],
        }

    for case in cases:
        relevant = labels.get(case["id"]) or []
        vector_rank = (
            cosine_rank(embed_query_fn(case["query"]), chunk_vectors, args.lexical_limit)
            if args.mode == "hybrid"
            else None
        )
        ranked = rrf_rank(
            case["query"],
            chunks,
            args.return_limit,
            args.lexical_limit,
            vector_rank=vector_rank,
            k=args.rrf_k,
        )
        per_case.append(
            {
                "id": case["id"],
                "query": case["query"],
                "relevant_count": len(relevant),
                "ranked_count": len(ranked),
                "top": ranked[:5],
                "top_citations": [cite(chunk_id) for chunk_id in ranked[:5]],
                **metrics(ranked, relevant, ks),
            }
        )

    scored = [item for item in per_case if item["relevant_count"] > 0]
    summary = {
        "cases": len(per_case),
        "scored_cases": len(scored),
        "unlabeled_cases": len(per_case) - len(scored),
        "chunks": len(chunks),
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "chunk_unit": "token",
        "mode": args.mode,
    }
    for key in [f"hit@{k}" for k in ks] + [f"recall@{k}" for k in (ks[-1],)] + ["mrr", "empty"]:
        summary[key] = round(
            sum(item[key] for item in scored) / len(scored), 4
        ) if scored else 0.0

    out_path = (
        RESULTS_DIR
        / f"metrics_{args.mode}_{args.chunk_size}t_{args.overlap}o_{timestamp}.json"
    )
    out_path.write_text(
        json.dumps({"summary": summary, "cases": per_case}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"明细: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
