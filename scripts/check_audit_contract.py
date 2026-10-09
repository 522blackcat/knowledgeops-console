"""Check that documented audit actions are implemented and protected."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RBAC_DOC = ROOT / "docs" / "RBAC.md"
OPERATIONS_DOC = ROOT / "docs" / "OPERATIONS.md"
APP_DIR = ROOT / "app"
MODEL_PATH = ROOT / "infrastructure" / "models.py"
MIGRATION_PATH = ROOT / "alembic" / "versions" / "0003_add_audit_logs.py"
AUDIT_API_PATH = APP_DIR / "audit_api.py"


def documented_actions() -> set[str]:
    text = RBAC_DOC.read_text(encoding="utf-8")
    match = re.search(r"## 审计(?P<body>.*?)(?:\n## |\Z)", text, re.S)
    if not match:
        return set()
    return set(re.findall(r"`([a-z_]+\.[a-z_]+)`", match.group("body")))


def app_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in APP_DIR.glob("*.py")
    )


def action_is_implemented(action: str, source: str) -> bool:
    if f'action="{action}"' in source or f"action='{action}'" in source:
        return True

    if action in {"approval.approved", "approval.rejected"}:
        return (
            'action=f"approval.{decision}"' in source
            and f'decision="{action.split(".")[1]}"' in source
        )

    return False


def main() -> int:
    failures: list[str] = []
    actions = documented_actions()
    source = app_source()

    if not actions:
        failures.append("no documented audit actions found in docs/RBAC.md")

    if "class AuditLog" not in MODEL_PATH.read_text(encoding="utf-8"):
        failures.append("AuditLog model is missing")

    migration = MIGRATION_PATH.read_text(encoding="utf-8") if MIGRATION_PATH.exists() else ""
    for token in [
        "audit_logs",
        "tenant_id",
        "actor_user_id",
        "actor_username",
        "action",
        "resource_type",
        "resource_id",
        "metadata_json",
        "created_at",
    ]:
        if token not in migration:
            failures.append(f"audit migration missing {token}")

    audit_api = AUDIT_API_PATH.read_text(encoding="utf-8")
    if "Permission.AUDIT_READ" not in audit_api:
        failures.append("audit API must require Permission.AUDIT_READ")

    for action in sorted(actions):
        if not action_is_implemented(action, source):
            failures.append(f"documented audit action is not implemented: {action}")

    operations = OPERATIONS_DOC.read_text(encoding="utf-8")
    if "GET /api/audit/logs" not in operations:
        failures.append("operations docs must mention GET /api/audit/logs")

    if failures:
        print("Audit contract check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"Audit contract check passed: {len(actions)} documented actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
