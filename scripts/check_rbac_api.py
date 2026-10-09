"""Run live RBAC checks through the public API."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_PASSWORD = "CHANGE_ME_TO_A_STRONG_PASSWORD"
TEST_PASSWORD = "RBAC_Test_12345"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 15,
    allow_http_error: bool = False,
) -> tuple[int, Any]:
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

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else None
            return int(response.status), payload
    except urllib.error.HTTPError as exc:
        if not allow_http_error:
            raise
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return int(exc.code), payload


def login(
    *,
    base_url: str,
    username: str,
    password: str,
) -> str:
    status, payload = request_json(
        "POST",
        f"{base_url}/api/auth/login",
        body={
            "username": username,
            "password": password,
        },
    )

    if status != 200:
        raise RuntimeError(
            f"login failed for {username}: {status}"
        )

    return str(payload["access_token"])


def ensure_user(
    *,
    base_url: str,
    admin_token: str,
    username: str,
    role: str,
) -> None:
    status, _ = request_json(
        "POST",
        f"{base_url}/api/admin/users",
        token=admin_token,
        body={
            "username": username,
            "password": TEST_PASSWORD,
            "role": role,
            "is_active": True,
        },
        allow_http_error=True,
    )

    if status not in {201, 409}:
        raise RuntimeError(
            f"failed to ensure {role} user {username}: HTTP {status}"
        )


def expect_status(
    *,
    label: str,
    actual: int,
    expected: set[int],
) -> None:
    if actual not in expected:
        raise AssertionError(
            f"{label}: expected {sorted(expected)}, got {actual}"
        )

    print(f"[ok] {label}: HTTP {actual}")


def create_temp_agent(
    *,
    base_url: str,
    token: str,
    role: str,
) -> int:
    status, _ = request_json(
        "POST",
        f"{base_url}/api/agents",
        token=token,
        body={
            "name": f"RBAC smoke {role} {int(time.time())}",
            "description": "RBAC live regression temporary Agent",
            "agent_type": "general",
            "configuration": {
                "knowledge_scope": "none",
                "system_prompt": "RBAC smoke prompt",
            },
        },
        allow_http_error=True,
    )
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check live API RBAC behavior."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
    )
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument(
        "--admin-password",
        default=DEFAULT_PASSWORD,
    )
    parser.add_argument(
        "--operator-username",
        default="rbac_operator_smoke",
    )
    parser.add_argument(
        "--viewer-username",
        default="rbac_viewer_smoke",
    )

    args = parser.parse_args()

    try:
        admin_token = login(
            base_url=args.base_url,
            username=args.admin_username,
            password=args.admin_password,
        )

        ensure_user(
            base_url=args.base_url,
            admin_token=admin_token,
            username=args.operator_username,
            role="operator",
        )
        ensure_user(
            base_url=args.base_url,
            admin_token=admin_token,
            username=args.viewer_username,
            role="viewer",
        )

        operator_token = login(
            base_url=args.base_url,
            username=args.operator_username,
            password=TEST_PASSWORD,
        )
        viewer_token = login(
            base_url=args.base_url,
            username=args.viewer_username,
            password=TEST_PASSWORD,
        )

        checks = [
            (
                "admin can read audit",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/audit/logs",
                    token=admin_token,
                    allow_http_error=True,
                )[0],
                {200},
            ),
            (
                "operator cannot read audit",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/audit/logs",
                    token=operator_token,
                    allow_http_error=True,
                )[0],
                {403},
            ),
            (
                "viewer cannot read audit",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/audit/logs",
                    token=viewer_token,
                    allow_http_error=True,
                )[0],
                {403},
            ),
            (
                "operator can create agent",
                lambda: create_temp_agent(
                    base_url=args.base_url,
                    token=operator_token,
                    role="operator",
                ),
                {201},
            ),
            (
                "viewer cannot create agent",
                lambda: create_temp_agent(
                    base_url=args.base_url,
                    token=viewer_token,
                    role="viewer",
                ),
                {403},
            ),
            (
                "operator can list approvals",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/approvals/pending",
                    token=operator_token,
                    allow_http_error=True,
                )[0],
                {200},
            ),
            (
                "operator can list recent approvals",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/approvals/recent",
                    token=operator_token,
                    allow_http_error=True,
                )[0],
                {200},
            ),
            (
                "viewer cannot list approvals",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/approvals/pending",
                    token=viewer_token,
                    allow_http_error=True,
                )[0],
                {403},
            ),
            (
                "viewer cannot list recent approvals",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/approvals/recent",
                    token=viewer_token,
                    allow_http_error=True,
                )[0],
                {403},
            ),
            (
                "viewer can read agents",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/agents",
                    token=viewer_token,
                    allow_http_error=True,
                )[0],
                {200},
            ),
            (
                "viewer can read knowledge bases",
                lambda: request_json(
                    "GET",
                    f"{args.base_url}/api/knowledge/bases",
                    token=viewer_token,
                    allow_http_error=True,
                )[0],
                {200},
            ),
        ]

        for label, call, expected in checks:
            expect_status(
                label=label,
                actual=call(),
                expected=expected,
            )

    except Exception as exc:
        print(
            f"RBAC API check failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    print("RBAC API check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
