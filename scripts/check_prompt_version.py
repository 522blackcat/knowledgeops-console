"""Check prompt_version traceability wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "agent_api": ROOT / "app" / "agent_api.py",
    "run_api": ROOT / "app" / "run_api.py",
    "worker": ROOT / "agent" / "worker.py",
    "completion": ROOT / "agent" / "completion.py",
    "e2e": ROOT / "scripts" / "check_agent_e2e.py",
}

REQUIRED = {
    "agent_api": [
        "ensure_prompt_version",
        "bump_prompt_version",
        "prompt_version",
    ],
    "run_api": [
        "prompt_version",
        "append_message",
        "run.stage",
    ],
    "worker": [
        "prompt_version",
        "build_initial_state",
        "approval.required",
        "rag.retrieved",
    ],
    "completion": [
        "prompt_version",
        "metadata",
        "append_message",
    ],
    "e2e": [
        "prompt_version",
        "User message does not include prompt_version",
        "Approval event did not expose prompt_version",
    ],
}


def main() -> int:
    failures: list[str] = []

    for name, path in FILES.items():
        if not path.exists():
            failures.append(f"{name} missing: {path}")
            continue

        text = path.read_text(encoding="utf-8")

        for marker in REQUIRED[name]:
            if marker not in text:
                failures.append(
                    f"{marker!r} missing from {name}"
                )

    if failures:
        print("Prompt version check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Prompt version check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
