"""Check the expected RBAC role-to-permission matrix."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rbac import Permission, ROLE_PERMISSIONS, has_permission  # noqa: E402


EXPECTED_ROLE_PERMISSIONS = {
    "admin": {permission for permission in Permission},
    "operator": {
        Permission.AGENT_READ,
        Permission.AGENT_WRITE,
        Permission.RUN_READ,
        Permission.RUN_CREATE,
        Permission.RUN_CANCEL,
        Permission.APPROVAL_READ,
        Permission.APPROVAL_REVIEW,
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_WRITE,
    },
    "viewer": {
        Permission.AGENT_READ,
        Permission.RUN_READ,
        Permission.KNOWLEDGE_READ,
    },
}


def main() -> int:
    failures: list[str] = []

    actual_roles = set(ROLE_PERMISSIONS)
    expected_roles = set(EXPECTED_ROLE_PERMISSIONS)
    if actual_roles != expected_roles:
        failures.append(
            "role set mismatch: "
            f"actual={sorted(actual_roles)} expected={sorted(expected_roles)}"
        )

    for role, expected_permissions in EXPECTED_ROLE_PERMISSIONS.items():
        actual_permissions = set(ROLE_PERMISSIONS.get(role, frozenset()))

        missing = expected_permissions - actual_permissions
        extra = actual_permissions - expected_permissions

        if missing:
            failures.append(
                f"{role} missing permissions: "
                + ", ".join(sorted(permission.value for permission in missing))
            )
        if extra:
            failures.append(
                f"{role} unexpected permissions: "
                + ", ".join(sorted(permission.value for permission in extra))
            )

        for permission in Permission:
            expected = permission in expected_permissions
            actual = has_permission(role, permission)
            if actual != expected:
                failures.append(
                    f"has_permission({role!r}, {permission.value!r}) "
                    f"returned {actual}, expected {expected}"
                )

    for permission in Permission:
        if has_permission("unknown-role", permission):
            failures.append(
                f"unknown-role unexpectedly has {permission.value}"
            )

    if failures:
        print("RBAC matrix check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "RBAC matrix check passed: "
        f"{len(EXPECTED_ROLE_PERMISSIONS)} roles, "
        f"{len(Permission)} permissions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
