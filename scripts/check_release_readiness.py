"""Run local release-readiness checks that do not start services."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    ("RBAC matrix", [sys.executable, "scripts/check_rbac_matrix.py"]),
    ("RBAC docs", [sys.executable, "scripts/check_rbac_docs.py"]),
    ("API permissions", [sys.executable, "scripts/check_api_permissions.py"]),
    ("RBAC API script", [sys.executable, "scripts/check_rbac_api_script.py"]),
    ("Audit contract", [sys.executable, "scripts/check_audit_contract.py"]),
    ("Agent audit diff", [sys.executable, "scripts/check_agent_audit_diff.py"]),
    ("Run events", [sys.executable, "scripts/check_run_events.py"]),
    ("RAG policy", [sys.executable, "scripts/check_rag_policy.py"]),
    ("Prompt version", [sys.executable, "scripts/check_prompt_version.py"]),
    ("Reindex workflow", [sys.executable, "scripts/check_reindex_workflow.py"]),
    ("Production runbook", [sys.executable, "scripts/check_production_runbook.py"]),
    ("Eval reports", [sys.executable, "scripts/check_eval_reports.py"]),
]


def main() -> int:
    failed: list[str] = []

    for name, command in CHECKS:
        print(f"== {name} ==", flush=True)
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failed.append(name)

    if failed:
        print("Release readiness failed: " + ", ".join(failed))
        return 1

    print("Release readiness checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
