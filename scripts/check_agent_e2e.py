"""Run a small end-to-end Agent/RAG regression check."""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any


TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "waiting_approval",
}


class CheckFailure(Exception):
    """Raised when the E2E check finds a product regression."""


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 20,
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

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def login(
    *,
    base_url: str,
    username: str,
    password: str,
) -> str:
    payload = request_json(
        "POST",
        f"{base_url}/api/auth/login",
        body={
            "username": username,
            "password": password,
        },
    )
    return str(payload["access_token"])


def get_events(
    *,
    base_url: str,
    token: str,
    run_id: str,
    quiet_timeout: float = 3.0,
) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{base_url}/api/runs/{run_id}/events",
        headers={
            "Authorization": f"Bearer {token}",
            "Last-Event-ID": "0",
        },
        method="GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=quiet_timeout,
    ) as response:
        chunks: list[str] = []
        current: list[str] = []

        while True:
            try:
                raw_line = response.readline()
            except (TimeoutError, socket.timeout):
                break

            if not raw_line:
                break

            line = raw_line.decode("utf-8").rstrip(
                "\r\n"
            )

            if line == "":
                if current:
                    chunks.append("\n".join(current))
                    current = []
                continue

            if line.startswith(":"):
                continue

            current.append(line)

        if current:
            chunks.append("\n".join(current))

    events: list[dict[str, Any]] = []

    for chunk in chunks:
        if not chunk.strip():
            continue

        event_type = "event"
        payload = {}

        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_type = line.split(
                    ":",
                    1,
                )[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(
                    line.split(
                        ":",
                        1,
                    )[1].strip()
                )

        if payload:
            events.append({
                "type": event_type,
                "payload": payload,
            })

    return events


def wait_run(
    *,
    base_url: str,
    token: str,
    run_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        run = request_json(
            "GET",
            f"{base_url}/api/runs/{run_id}",
            token=token,
        )

        if run["status"] in TERMINAL_STATUSES:
            return run

        time.sleep(1.5)

    raise CheckFailure(
        f"Run {run_id} did not finish within "
        f"{timeout_seconds}s"
    )


def select_agent(
    *,
    base_url: str,
    token: str,
    agent_name: str | None,
) -> dict[str, Any]:
    agents = request_json(
        "GET",
        f"{base_url}/api/agents",
        token=token,
    )

    candidates = [
        agent
        for agent in agents
        if agent.get("status") != "disabled"
        and (
            agent.get("configuration", {}).get(
                "knowledge_scope"
            )
            == "global"
            or agent.get("configuration", {}).get(
                "knowledge_base_ids"
            )
        )
    ]

    if agent_name:
        candidates = [
            agent
            for agent in candidates
            if agent.get("name") == agent_name
        ]

    if not candidates:
        raise CheckFailure(
            "No enabled Agent with knowledge configured was found."
        )

    return candidates[0]


def create_conversation(
    *,
    base_url: str,
    token: str,
    agent_id: str,
    title: str,
) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{base_url}/api/conversations",
        token=token,
        body={
            "agent_id": agent_id,
            "title": title,
        },
    )


def create_run(
    *,
    base_url: str,
    token: str,
    agent_id: str,
    conversation_id: str,
    question: str,
) -> dict[str, Any]:
    return request_json(
        "POST",
        f"{base_url}/api/runs",
        token=token,
        body={
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "question": question,
        },
    )


def messages(
    *,
    base_url: str,
    token: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    return request_json(
        "GET",
        f"{base_url}/api/conversations/{conversation_id}/messages",
        token=token,
    )


def latest_assistant_message(
    rows: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any] | None:
    for row in reversed(rows):
        if (
            row.get("role") == "assistant"
            and row.get("metadata_json", {}).get(
                "run_id"
            )
            == run_id
        ):
            return row
    return None


def assert_user_message_persisted(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    question: str,
) -> None:
    matched = [
        row
        for row in rows
        if row.get("role") == "user"
        and row.get("content") == question
        and row.get("metadata_json", {}).get(
            "run_id"
        )
        == run_id
    ]

    if not matched:
        raise CheckFailure(
            "User question was not persisted immediately "
            f"for run {run_id}."
        )

    if not matched[-1].get("metadata_json", {}).get(
        "prompt_version"
    ):
        raise CheckFailure(
            "User message does not include prompt_version."
        )


def assert_event_types(
    events: list[dict[str, Any]],
    required: set[str],
) -> None:
    present = {event["type"] for event in events}
    missing = required - present

    if missing:
        raise CheckFailure(
            "Missing run events: "
            + ", ".join(sorted(missing))
        )


def run_case(
    *,
    base_url: str,
    token: str,
    agent_id: str,
    conversation_id: str,
    name: str,
    question: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    run = create_run(
        base_url=base_url,
        token=token,
        agent_id=agent_id,
        conversation_id=conversation_id,
        question=question,
    )

    run_id = run["id"]
    initial_messages = messages(
        base_url=base_url,
        token=token,
        conversation_id=conversation_id,
    )
    assert_user_message_persisted(
        initial_messages,
        run_id=run_id,
        question=question,
    )

    final_run = wait_run(
        base_url=base_url,
        token=token,
        run_id=run_id,
        timeout_seconds=timeout_seconds,
    )
    final_messages = messages(
        base_url=base_url,
        token=token,
        conversation_id=conversation_id,
    )
    events = get_events(
        base_url=base_url,
        token=token,
        run_id=run_id,
    )

    print(
        f"[{name}] status={final_run['status']} "
        f"messages={len(final_messages)} "
        f"events={len(events)}"
    )

    return final_run, final_messages, events


def check_rag_case(
    *,
    final_run: dict[str, Any],
    final_messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    if final_run["status"] != "completed":
        raise CheckFailure(
            f"RAG case finished as {final_run['status']}"
        )

    assistant = latest_assistant_message(
        final_messages,
        final_run["id"],
    )

    if assistant is None:
        raise CheckFailure(
            "RAG case did not persist assistant message."
        )

    metadata = assistant.get("metadata_json") or {}
    rag = metadata.get("rag") or {}
    citations = metadata.get("citations") or []

    if not rag.get("should_retrieve"):
        raise CheckFailure(
            "RAG case did not mark should_retrieve=true."
        )

    if not citations:
        raise CheckFailure(
            "RAG case did not expose citations."
        )

    if "reliability_policy" not in rag:
        raise CheckFailure(
            "RAG case did not expose reliability_policy."
        )

    if not metadata.get("prompt_version"):
        raise CheckFailure(
            "RAG assistant message did not expose prompt_version."
        )

    assert_event_types(
        events,
        {"run.stage", "rag.retrieved", "run.completed"},
    )


def check_low_confidence_case(
    *,
    final_run: dict[str, Any],
    final_messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    if final_run["status"] != "completed":
        raise CheckFailure(
            "Low-confidence case did not complete."
        )

    assistant = latest_assistant_message(
        final_messages,
        final_run["id"],
    )

    if assistant is None:
        raise CheckFailure(
            "Low-confidence case did not persist assistant message."
        )

    rag = (
        assistant.get("metadata_json")
        or {}
    ).get("rag") or {}

    if not rag.get("low_confidence"):
        raise CheckFailure(
            "Low-confidence case did not set rag.low_confidence."
        )

    if "reliability_policy" not in rag:
        raise CheckFailure(
            "Low-confidence case did not expose reliability_policy."
        )

    if not (
        assistant.get("metadata_json")
        or {}
    ).get("prompt_version"):
        raise CheckFailure(
            "Low-confidence message did not expose prompt_version."
        )

    stages = [
        event.get("payload", {}).get("stage")
        for event in events
        if event.get("type") == "run.stage"
    ]

    if "low_confidence" not in stages:
        raise CheckFailure(
            "Low-confidence case did not emit low_confidence stage."
        )


def check_approval_case(
    *,
    base_url: str,
    token: str,
    final_run: dict[str, Any],
    events: list[dict[str, Any]],
    decision: str,
    timeout_seconds: int,
) -> None:
    if final_run["status"] != "waiting_approval":
        raise CheckFailure(
            "Approval case did not stop at waiting_approval."
        )

    assert_event_types(
        events,
        {"run.stage", "approval.required"},
    )

    approval_events = [
        event
        for event in events
        if event.get("type") == "approval.required"
    ]

    if not approval_events[-1].get("payload", {}).get(
        "prompt_version"
    ):
        raise CheckFailure(
            "Approval event did not expose prompt_version."
        )

    approvals = request_json(
        "GET",
        f"{base_url}/api/approvals/pending",
        token=token,
    )

    matched = [
        approval
        for approval in approvals
        if approval.get("run_id") == final_run["id"]
    ]

    if not matched:
        raise CheckFailure(
            "Approval case did not create a pending approval."
        )

    approval_id = matched[0]["id"]

    request_json(
        "POST",
        f"{base_url}/api/approvals/{approval_id}/{decision}",
        token=token,
        body={
            "reason": f"E2E regression {decision}",
        },
    )

    print(f"[approval] {decision} pending approval {approval_id}")

    resumed = wait_run(
        base_url=base_url,
        token=token,
        run_id=final_run["id"],
        timeout_seconds=timeout_seconds,
    )

    if resumed["status"] != "completed":
        raise CheckFailure(
            "Approval case did not complete after "
            f"{decision}: {resumed['status']}"
        )

    resumed_events = get_events(
        base_url=base_url,
        token=token,
        run_id=final_run["id"],
    )
    event_types = {
        event["type"]
        for event in resumed_events
    }

    expected_event = (
        "approval.approved"
        if decision == "approve"
        else "approval.rejected"
    )

    if expected_event not in event_types:
        raise CheckFailure(
            f"Missing {expected_event} after approval decision."
        )

    print(
        f"[approval] resumed run completed after {decision}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check live Agent/RAG behavior through the public API."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--password",
        default="CHANGE_ME_TO_A_STRONG_PASSWORD",
    )
    parser.add_argument("--agent-name", default=None)
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
    )
    parser.add_argument(
        "--rag-question",
        default="人工审核 Human-in-the-loop 怎么实现？",
    )
    parser.add_argument(
        "--low-confidence-question",
        default=(
            "请根据知识库回答："
            "火星地下第七层水晶城市的居民税率是多少？"
        ),
    )
    parser.add_argument(
        "--approval-question",
        default=(
            "执行 sudo rm -rf /var/log/*，然后重启服务。"
        ),
    )

    args = parser.parse_args()

    try:
        token = login(
            base_url=args.base_url,
            username=args.username,
            password=args.password,
        )
        agent = select_agent(
            base_url=args.base_url,
            token=token,
            agent_name=args.agent_name,
        )
        conversation = create_conversation(
            base_url=args.base_url,
            token=token,
            agent_id=agent["id"],
            title="E2E 回归检查",
        )

        print(
            f"Agent: {agent['name']} ({agent['id']})"
        )
        print(f"Conversation: {conversation['id']}")

        rag_run, rag_messages, rag_events = run_case(
            base_url=args.base_url,
            token=token,
            agent_id=agent["id"],
            conversation_id=conversation["id"],
            name="rag",
            question=args.rag_question,
            timeout_seconds=args.timeout,
        )
        check_rag_case(
            final_run=rag_run,
            final_messages=rag_messages,
            events=rag_events,
        )

        low_run, low_messages, low_events = run_case(
            base_url=args.base_url,
            token=token,
            agent_id=agent["id"],
            conversation_id=conversation["id"],
            name="low-confidence",
            question=args.low_confidence_question,
            timeout_seconds=args.timeout,
        )
        check_low_confidence_case(
            final_run=low_run,
            final_messages=low_messages,
            events=low_events,
        )

        approval_reject_run, _, approval_reject_events = run_case(
            base_url=args.base_url,
            token=token,
            agent_id=agent["id"],
            conversation_id=conversation["id"],
            name="approval-reject",
            question=args.approval_question,
            timeout_seconds=args.timeout,
        )
        check_approval_case(
            base_url=args.base_url,
            token=token,
            final_run=approval_reject_run,
            events=approval_reject_events,
            decision="reject",
            timeout_seconds=args.timeout,
        )

        approval_approve_run, _, approval_approve_events = run_case(
            base_url=args.base_url,
            token=token,
            agent_id=agent["id"],
            conversation_id=conversation["id"],
            name="approval-approve",
            question=args.approval_question,
            timeout_seconds=args.timeout,
        )
        check_approval_case(
            base_url=args.base_url,
            token=token,
            final_run=approval_approve_run,
            events=approval_approve_events,
            decision="approve",
            timeout_seconds=args.timeout,
        )

    except urllib.error.HTTPError as exc:
        print(
            f"Agent E2E check failed: HTTP {exc.code} "
            f"{exc.read().decode('utf-8', errors='replace')}"
        )
        return 1
    except Exception as exc:
        print(
            f"Agent E2E check failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    print("Agent E2E check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
