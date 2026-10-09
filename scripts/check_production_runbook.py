"""Check production gap runbook contains required commands."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "PRODUCTION_GAP_RUNBOOK.md"

REQUIRED_MARKERS = [
    "check_runtime_connectivity.py",
    "check_release_readiness.py",
    "check_agent_e2e.py",
    "check_rbac_api.py",
    "rag_eval_runner.py",
    "reindex_documents.py",
    "warm_rag_models.py",
    "prompt_version",
    "configuration_diff",
    "docker compose down -v",
    "pg_dump",
]


def main() -> int:
    failures: list[str] = []
    text = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else ""

    if not text:
        failures.append("docs/PRODUCTION_GAP_RUNBOOK.md is missing or empty")

    for marker in REQUIRED_MARKERS:
        if marker not in text:
            failures.append(f"{marker!r} missing from production runbook")

    if failures:
        print("Production runbook check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Production runbook check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
