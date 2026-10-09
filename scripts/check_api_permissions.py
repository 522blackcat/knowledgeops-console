"""Check that business API routes declare an RBAC permission dependency."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"

ROUTE_PATTERN = re.compile(
    r"^@(router|app)\.(get|post|put|patch|delete)\(",
    re.MULTILINE,
)

PUBLIC_ROUTE_ALLOWLIST = {
    ("auth_api.py", "/login"),
    ("auth_api.py", "/me"),
    ("main.py", "/health/live"),
}


def route_path(block: str) -> str:
    match = re.search(r"@\w+\.\w+\(\s*[\"']([^\"']+)[\"']", block)
    return match.group(1) if match else "<unknown>"


def route_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    matches = list(ROUTE_PATTERN.finditer(text))
    blocks: list[str] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append(text[start:end])

    return blocks


def main() -> int:
    missing: list[str] = []

    for path in sorted(APP_DIR.glob("*.py")):
        if not (path.name.endswith("_api.py") or path.name == "main.py"):
            continue

        for block in route_blocks(path):
            path_text = route_path(block)
            if (path.name, path_text) in PUBLIC_ROUTE_ALLOWLIST:
                continue

            if "require_permission(" not in block:
                missing.append(f"{path.name}:{path_text}")

    if missing:
        print("API permission check failed. Missing require_permission:")
        for item in missing:
            print(f"- {item}")
        return 1

    print("API permission check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
