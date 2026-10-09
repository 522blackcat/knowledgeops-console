"""Check Docker image split configuration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
API_REQUIREMENTS = ROOT / "requirements-api.txt"
AGENT_REQUIREMENTS = ROOT / "requirements-agent.txt"
DOCS = ROOT / "docs" / "IMAGE_SPLIT.md"
AGENT_WORKER = ROOT / "agent" / "worker.py"

REQUIRED_MARKERS = {
    DOCKERFILE: [
        "FROM python:3.11-slim AS api",
        "FROM python:3.11-slim AS agent",
        "requirements-api.txt",
        "requirements-agent.txt",
        "FROM python:3.11-slim AS lite",
    ],
    COMPOSE: [
        "target: api",
        "target: agent",
        "target: full",
        "target: lite",
        "RAG_RETRIEVAL_URL: http://rag-api:8010",
        "uvicorn, rag.service:app",
    ],
    API_REQUIREMENTS: [
        "intentionally excludes sentence-transformers",
        "fastapi",
        "rank-bm25",
    ],
    AGENT_REQUIREMENTS: [
        "intentionally excludes sentence-transformers",
        "langgraph",
        "openai",
        "mcp",
    ],
    DOCS: [
        "api",
        "rag-api",
        "rag-worker",
        "agent-worker",
        "RAG_RETRIEVAL_URL",
    ],
    AGENT_WORKER: [
        "settings.rag_retrieval_url",
        "from rag.retrieval import",
        "hybrid_retrieve",
    ],
}

FORBIDDEN_API_REQUIREMENTS = [
    "sentence-transformers>=",
    "transformers>=",
    "torch>=",
]


def main() -> int:
    failures: list[str] = []

    for path, markers in REQUIRED_MARKERS.items():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for marker in markers:
            if marker not in text:
                failures.append(f"{marker!r} missing from {path.name}")

    for path in [
        API_REQUIREMENTS,
        AGENT_REQUIREMENTS,
    ]:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for marker in FORBIDDEN_API_REQUIREMENTS:
            if marker in text:
                failures.append(
                    f"{marker!r} must not be in {path.name}"
                )

    compose_text = (
        COMPOSE.read_text(encoding="utf-8")
        if COMPOSE.exists()
        else ""
    )
    if compose_text.count(
        "RAG_RETRIEVAL_URL: http://rag-api:8010"
    ) < 2:
        failures.append(
            "api and agent-worker must both use RAG_RETRIEVAL_URL"
        )

    if failures:
        print("Image split check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Image split check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
