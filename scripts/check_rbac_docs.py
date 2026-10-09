"""Check that RBAC docs, backend permissions, and frontend gates stay aligned."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "RBAC.md"
FRONTEND_PATH = ROOT / "frontend" / "src" / "App.vue"

sys.path.insert(0, str(ROOT))

from app.rbac import Permission, ROLE_PERMISSIONS  # noqa: E402


EXPECTED_FRONTEND_GATES = {
    "canManageAgents",
    "canManageKnowledge",
    "canReviewApprovals",
    "canCreateRuns",
    "canManageUsers",
}


def fail(message: str) -> None:
    print(f"RBAC docs check failed: {message}")
    raise SystemExit(1)


def main() -> int:
    if not DOC_PATH.exists():
        fail(f"missing {DOC_PATH}")
    if not FRONTEND_PATH.exists():
        fail(f"missing {FRONTEND_PATH}")

    doc = DOC_PATH.read_text(encoding="utf-8")
    frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    backend_permissions = {permission.value for permission in Permission}
    documented_permissions = set(re.findall(r"`([a-z_]+:[a-z_]+)`", doc))

    missing_permissions = backend_permissions - documented_permissions
    extra_permissions = documented_permissions - backend_permissions

    if missing_permissions:
        fail("permissions missing from docs: " + ", ".join(sorted(missing_permissions)))
    if extra_permissions:
        fail("unknown permissions in docs: " + ", ".join(sorted(extra_permissions)))

    documented_roles = set(re.findall(r"`(admin|operator|viewer)`", doc))
    backend_roles = set(ROLE_PERMISSIONS)

    missing_roles = backend_roles - documented_roles
    extra_roles = documented_roles - backend_roles

    if missing_roles:
        fail("roles missing from docs: " + ", ".join(sorted(missing_roles)))
    if extra_roles:
        fail("unknown roles in docs: " + ", ".join(sorted(extra_roles)))

    missing_gates = {
        gate for gate in EXPECTED_FRONTEND_GATES
        if gate not in frontend or gate not in doc
    }
    if missing_gates:
        fail("frontend gates missing from docs or App.vue: " + ", ".join(sorted(missing_gates)))

    print(
        "RBAC docs check passed: "
        f"{len(backend_roles)} roles, "
        f"{len(backend_permissions)} permissions, "
        f"{len(EXPECTED_FRONTEND_GATES)} frontend gates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
