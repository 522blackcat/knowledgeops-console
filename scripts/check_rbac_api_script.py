"""Check live RBAC API regression script assets."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_rbac_api.py"
DOCS = ROOT / "docs" / "OPERATIONS.md"

SCRIPT_MARKERS = [
    "rbac_operator_smoke",
    "rbac_viewer_smoke",
    "operator can create agent",
    "viewer cannot create agent",
    "viewer cannot list approvals",
    "admin can read audit",
]

DOC_MARKERS = [
    "check_rbac_api.py",
    "operator",
    "viewer",
    "RBAC",
]


def main() -> int:
    failures: list[str] = []
    script = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
    docs = DOCS.read_text(encoding="utf-8") if DOCS.exists() else ""

    for marker in SCRIPT_MARKERS:
        if marker not in script:
            failures.append(f"{marker!r} missing from RBAC API script")

    for marker in DOC_MARKERS:
        if marker not in docs:
            failures.append(f"{marker!r} missing from operations docs")

    if failures:
        print("RBAC API script check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("RBAC API script check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
