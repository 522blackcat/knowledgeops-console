"""Check that RAG reliability policy is wired through code and docs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "config": ROOT / "infrastructure" / "config.py",
    "parser": ROOT / "rag" / "parser.py",
    "worker": ROOT / "agent" / "worker.py",
    "frontend": ROOT / "frontend" / "src" / "App.vue",
    "docs": ROOT / "docs" / "RUN_EVENTS.md",
}

REQUIRED = {
    "config": [
        "rag_min_score",
        "rag_relative_score_ratio",
        "rag_display_top_k",
        "rag_context_top_k",
    ],
    "parser": [
        "parse_html_file",
        "parse_docx_file",
        "heading",
        "heading_level",
    ],
    "worker": [
        "rag_reliability_policy",
        "explain_rag_evidence",
        "evidence_decision",
        "retrieval_stats",
        "knowledge_base_scope",
        "document_version",
        "section_label",
        "location_label",
        "reliability_policy",
        "threshold",
        "top_score",
        "relative_score_ratio",
    ],
    "frontend": [
        "ragPolicyLine",
        "ragEvidenceLine",
        "rejectedEvidence",
        "retrievalStatsLine",
        "citationMeta",
        "evidenceReason",
        "versionState",
        "section_label",
        "location_label",
        "当前检索仍使用",
        "历史版本",
        "被过滤的知识库片段",
        "可靠阈值",
        "ragMeta(m).message",
    ],
    "docs": [
        "reliability_policy",
        "min_score",
        "relative_score_ratio",
        "threshold",
        "max_display_hits",
        "retrieval_stats",
        "document_version",
        "section_label",
    ],
}


def main() -> int:
    failures: list[str] = []

    for name, path in FILES.items():
        if not path.exists():
            failures.append(f"{name} file missing: {path}")
            continue

        text = path.read_text(encoding="utf-8")

        for marker in REQUIRED[name]:
            if marker not in text:
                failures.append(
                    f"{marker!r} missing from {name}"
                )

    if failures:
        print("RAG policy check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("RAG policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
