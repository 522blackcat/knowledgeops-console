"""Check live Docker runtime connectivity for local development."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:8080"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 10,
) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def request_status(url: str, timeout: int = 10) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return int(response.status)


def run_compose_exec(command: str) -> tuple[int, str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "agent-worker",
            "python",
            "-c",
            command,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode, result.stdout.strip()


def main() -> int:
    failures: list[str] = []

    try:
        status = request_status(f"{API_URL}/health/live")
        print(f"API health: {status}")
    except Exception as exc:
        failures.append(f"API health failed: {type(exc).__name__}: {exc}")

    try:
        status = request_status(FRONTEND_URL)
        print(f"Frontend: {status}")
    except Exception as exc:
        failures.append(f"Frontend failed: {type(exc).__name__}: {exc}")

    token = ""
    try:
        payload = request_json(
            "POST",
            f"{API_URL}/api/auth/login",
            body={
                "username": "admin",
                "password": "CHANGE_ME_TO_A_STRONG_PASSWORD",
            },
        )
        token = str(payload["access_token"])
        print("Login: ok")
    except urllib.error.HTTPError as exc:
        failures.append(f"Login failed: HTTP {exc.code}")
    except Exception as exc:
        failures.append(f"Login failed: {type(exc).__name__}: {exc}")

    if token:
        try:
            me = request_json("GET", f"{API_URL}/api/auth/me", token=token)
            print(f"Current user: {me['username']} ({me['role']})")
        except Exception as exc:
            failures.append(f"Current user failed: {type(exc).__name__}: {exc}")

        try:
            audit = request_json("GET", f"{API_URL}/api/audit/logs", token=token)
            print(f"Audit logs: {len(audit)} rows")
        except Exception as exc:
            failures.append(f"Audit logs failed: {type(exc).__name__}: {exc}")

        try:
            reports = request_json(
                "GET",
                f"{API_URL}/api/rag/eval/reports",
                token=token,
            )
            print(f"Eval reports: {len(reports.get('reports') or [])}")
        except Exception as exc:
            failures.append(f"Eval reports failed: {type(exc).__name__}: {exc}")

    code, output = run_compose_exec(
        "import httpx; "
        "from infrastructure.config import get_settings; "
        "s=get_settings(); "
        "url=str(s.ollama_base_url).rstrip('/'); "
        "base=url[:-3] if url.endswith('/v1') else url; "
        "tags=base+'/api/tags'; "
        "print('Ollama tags URL:', tags); "
        "r=httpx.get(tags, timeout=5); "
        "print('Ollama status:', r.status_code)"
    )
    if code == 0:
        print(output)
    else:
        failures.append(
            "Agent worker cannot reach Ollama. "
            "Start Ollama on the host or fix OLLAMA_BASE_URL. "
            f"Details: {output}"
        )

    if failures:
        print("Runtime connectivity check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Runtime connectivity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
