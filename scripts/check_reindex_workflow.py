"""Check knowledge document reindex workflow assets."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = ROOT / "scripts" / "reindex_documents.py"
API = ROOT / "app" / "knowledge_api.py"
FRONTEND = ROOT / "frontend" / "src" / "App.vue"
DOCS = ROOT / "docs" / "OPERATIONS.md"

REQUIRED_SCRIPT_MARKERS = [
    "IngestJob",
    "KnowledgeDocument",
    "document.current_version + 1",
    "rag:ingest:wakeup",
    "--dry-run",
    "--document-id",
    "--all-ready",
]

REQUIRED_DOC_MARKERS = [
    "reindex_documents.py",
    "POST /api/knowledge/documents/{document_id}/reindex",
    "点击“重建”",
    "--dry-run",
    "--document-id",
    "--all-ready",
]

REQUIRED_API_MARKERS = [
    "/documents/{document_id}/reindex",
    "document.reindex",
    "func.max",
    "ACTIVE_INGEST_STATUSES",
    "rag:ingest:wakeup",
]

REQUIRED_FRONTEND_MARKERS = [
    "reindexDocument",
    "canReindexDocument",
    "重建索引",
    "新版本完成前，旧版本仍保持可检索",
]


def main() -> int:
    failures: list[str] = []

    script = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
    api = API.read_text(encoding="utf-8") if API.exists() else ""
    frontend = FRONTEND.read_text(encoding="utf-8") if FRONTEND.exists() else ""
    docs = DOCS.read_text(encoding="utf-8") if DOCS.exists() else ""

    for marker in REQUIRED_SCRIPT_MARKERS:
        if marker not in script:
            failures.append(f"{marker!r} missing from reindex script")

    for marker in REQUIRED_DOC_MARKERS:
        if marker not in docs:
            failures.append(f"{marker!r} missing from operations docs")

    for marker in REQUIRED_API_MARKERS:
        if marker not in api:
            failures.append(f"{marker!r} missing from knowledge API")

    for marker in REQUIRED_FRONTEND_MARKERS:
        if marker not in frontend:
            failures.append(f"{marker!r} missing from frontend")

    if failures:
        print("Reindex workflow check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Reindex workflow check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
