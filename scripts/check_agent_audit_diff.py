"""Check Agent update audit diff wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_API = ROOT / "app" / "agent_api.py"
RBAC_DOC = ROOT / "docs" / "RBAC.md"

API_MARKERS = [
    "AUDITED_CONFIGURATION_KEYS",
    "agent_audit_changes",
    "configuration_diff",
    "changed_keys",
    "system_prompt",
    "prompt_version",
    "knowledge_scope",
    "knowledge_base_ids",
    "retrieval_mode",
]

DOC_MARKERS = [
    "configuration_diff",
    "prompt_version",
    "system_prompt",
    "knowledge_scope",
]


def main() -> int:
    failures: list[str] = []
    source = AGENT_API.read_text(encoding="utf-8") if AGENT_API.exists() else ""
    doc = RBAC_DOC.read_text(encoding="utf-8") if RBAC_DOC.exists() else ""

    for marker in API_MARKERS:
        if marker not in source:
            failures.append(f"{marker!r} missing from app/agent_api.py")

    for marker in DOC_MARKERS:
        if marker not in doc:
            failures.append(f"{marker!r} missing from docs/RBAC.md")

    if failures:
        print("Agent audit diff check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Agent audit diff check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
