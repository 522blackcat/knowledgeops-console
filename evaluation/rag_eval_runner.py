"""Batch RAG evaluation runner.

Runs JSON-defined retrieval cases against /api/rag/eval and writes a result
report that can be compared across changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


DEFAULT_CASE_FILE = Path(__file__).with_name("rag_eval_cases.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("results")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run JSON-defined RAG retrieval evaluations."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("EVAL_BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument(
        "--username",
        default=os.getenv(
            "EVAL_USERNAME",
            os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"),
        ),
    )
    parser.add_argument(
        "--password",
        default=os.getenv(
            "EVAL_PASSWORD",
            os.getenv(
                "BOOTSTRAP_ADMIN_PASSWORD",
                "CHANGE_ME_TO_A_STRONG_PASSWORD",
            ),
        ),
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASE_FILE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("EVAL_TIMEOUT_SECONDS", "120")),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run only the first N cases. 0 means all cases.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=float(os.getenv("EVAL_FAIL_UNDER", "0.8")),
        help="Minimum pass rate required for exit code 0.",
    )
    parser.add_argument(
        "--use-reranker",
        action="store_true",
        default=(
            os.getenv("EVAL_USE_RERANKER", "")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        ),
        help="Enable CrossEncoder reranking. Off by default to keep eval fast.",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=["lexical", "hybrid"],
        default=os.getenv("EVAL_RETRIEVAL_MODE", "lexical"),
        help=(
            "Retrieval mode for eval. lexical uses BM25 only and avoids "
            "embedding model cold starts; hybrid uses vector retrieval."
        ),
    )
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path} must contain a non-empty cases array.")
    return cases


def login(
    client: httpx.Client,
    username: str,
    password: str,
) -> str:
    last_error: Exception | None = None
    response: httpx.Response | None = None

    for attempt in range(1, 6):
        try:
            response = client.post(
                "/api/auth/login",
                json={
                    "username": username,
                    "password": password,
                },
            )
            break
        except (
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
        ) as exc:
            last_error = exc
            time.sleep(min(attempt, 3))

    if response is None:
        raise RuntimeError(
            "Login failed after retries: "
            f"{type(last_error).__name__}: {last_error}"
        )

    if response.status_code == 502:
        raise RuntimeError(
            "Login got 502 Bad Gateway. If you are using "
            "http://localhost:8080, that is the frontend nginx proxy. "
            "Run against the API directly with --base-url http://localhost:8000 "
            "or check the frontend proxy/API container network."
        )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Login succeeded but access_token is missing.")
    return token


def call_rag_eval(
    client: httpx.Client,
    token: str,
    case: dict[str, Any],
    retrieval_mode: str,
    use_reranker: bool,
) -> dict[str, Any]:
    response = client.post(
        "/api/rag/eval",
        headers={
            "Authorization": f"Bearer {token}",
        },
        json={
            "query": case["query"],
            "knowledge_scope": case.get("knowledge_scope", "global"),
            "knowledge_base_ids": case.get("knowledge_base_ids", []),
            "retrieval_mode": case.get(
                "retrieval_mode",
                retrieval_mode,
            ),
            "use_reranker": bool(
                case.get("use_reranker", use_reranker)
            ),
        },
    )
    response.raise_for_status()
    return response.json()


def judge_case(
    case: dict[str, Any],
    response: dict[str, Any],
    elapsed_ms: int,
) -> dict[str, Any]:
    results = response.get("results") or []
    hit_count = int(response.get("hit_count") or 0)
    result_texts = "\n".join(
        str(item.get("text") or item.get("preview") or "")
        for item in results
    )
    filenames = "\n".join(
        str(item.get("filename") or "")
        for item in results
    )
    search_text = result_texts + "\n" + filenames
    top_text = ""
    if results:
        top = results[0]
        top_text = "\n".join([
            str(top.get("text") or top.get("preview") or ""),
            str(top.get("filename") or ""),
        ])

    min_hits = int(case.get("min_hits", 1))
    expected_terms = [
        str(item).strip()
        for item in case.get("expected_terms", [])
        if str(item).strip()
    ]
    expected_any_terms = [
        str(item).strip()
        for item in case.get("expected_any_terms", [])
        if str(item).strip()
    ]
    expected_top_any_terms = [
        str(item).strip()
        for item in case.get("expected_top_any_terms", [])
        if str(item).strip()
    ]
    forbidden_top_terms = [
        str(item).strip()
        for item in case.get("forbidden_top_terms", [])
        if str(item).strip()
    ]
    expected_filenames = [
        str(item).strip()
        for item in case.get("expected_filenames", [])
        if str(item).strip()
    ]

    failures: list[str] = []
    if hit_count < min_hits:
        failures.append(f"hit_count {hit_count} < min_hits {min_hits}")

    missing_terms = [
        term
        for term in expected_terms
        if term not in search_text
    ]
    if missing_terms:
        failures.append(
            "missing expected_terms: " + ", ".join(missing_terms)
        )

    if expected_any_terms and not any(
        term in search_text
        for term in expected_any_terms
    ):
        failures.append(
            "missing any expected_any_terms: "
            + ", ".join(expected_any_terms)
        )

    if expected_top_any_terms and not any(
        term in top_text
        for term in expected_top_any_terms
    ):
        failures.append(
            "top result missing any expected_top_any_terms: "
            + ", ".join(expected_top_any_terms)
        )

    present_forbidden_terms = [
        term
        for term in forbidden_top_terms
        if term in top_text
    ]
    if present_forbidden_terms:
        failures.append(
            "top result contains forbidden_top_terms: "
            + ", ".join(present_forbidden_terms)
        )

    missing_files = [
        filename
        for filename in expected_filenames
        if filename not in filenames
    ]
    if missing_files:
        failures.append(
            "missing expected_filenames: " + ", ".join(missing_files)
        )

    return {
        "id": case["id"],
        "query": case["query"],
        "passed": not failures,
        "failures": failures,
        "elapsed_ms": elapsed_ms,
        "hit_count": hit_count,
        "top_results": results[:5],
    }


def run_case(
    client: httpx.Client,
    token: str,
    case: dict[str, Any],
    retrieval_mode: str,
    use_reranker: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = call_rag_eval(
            client,
            token,
            case,
            retrieval_mode,
            use_reranker,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return judge_case(case, response, elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "id": case.get("id", "unknown"),
            "query": case.get("query"),
            "passed": False,
            "failures": [f"{type(exc).__name__}: {exc}"],
            "elapsed_ms": elapsed_ms,
            "hit_count": 0,
            "top_results": [],
        }


def write_report(
    output_dir: Path,
    report: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"rag_eval_{timestamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def first_result_value(
    result: dict[str, Any],
    key: str,
) -> str:
    top_results = result.get("top_results") or []
    if not top_results:
        return ""
    return str(top_results[0].get(key) or "")


def write_summary_tables(
    output_dir: Path,
    report_path: Path,
    report: dict[str, Any],
) -> tuple[Path, Path]:
    csv_path = report_path.with_suffix(".csv")
    md_path = report_path.with_suffix(".md")
    rows = report["results"]

    columns = [
        "id",
        "passed",
        "hit_count",
        "elapsed_ms",
        "query",
        "top_filename",
        "top_score",
        "failures",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for result in rows:
            writer.writerow(
                {
                    "id": result["id"],
                    "passed": "PASS" if result["passed"] else "FAIL",
                    "hit_count": result["hit_count"],
                    "elapsed_ms": result["elapsed_ms"],
                    "query": result.get("query") or "",
                    "top_filename": first_result_value(result, "filename"),
                    "top_score": first_result_value(result, "score"),
                    "failures": "; ".join(result.get("failures") or []),
                }
            )

    summary = report["summary"]
    lines = [
        "# RAG Eval Summary",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- Retrieval mode: {summary['retrieval_mode']}",
        f"- Use reranker: {summary['use_reranker']}",
        "",
        "| ID | Result | Hits | Time | Query | Top file | Failures |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in rows:
        status = "PASS" if result["passed"] else "FAIL"
        query = str(result.get("query") or "").replace("|", "\\|")
        top_filename = first_result_value(result, "filename").replace("|", "\\|")
        failures = "; ".join(result.get("failures") or []).replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result["id"]),
                    status,
                    str(result["hit_count"]),
                    str(result["elapsed_ms"]),
                    query,
                    top_filename,
                    failures,
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return csv_path, md_path


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
        trust_env=False,
    ) as client:
        token = login(client, args.username, args.password)
        results = [
            run_case(
                client,
                token,
                case,
                args.retrieval_mode,
                args.use_reranker,
            )
            for case in cases
        ]

    passed = sum(1 for item in results if item["passed"])
    total = len(results)
    pass_rate = passed / total if total else 0
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "case_file": str(args.cases),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": pass_rate,
            "fail_under": args.fail_under,
            "retrieval_mode": args.retrieval_mode,
            "use_reranker": args.use_reranker,
        },
        "results": results,
    }
    output_path = write_report(args.output_dir, report)
    csv_path, md_path = write_summary_tables(
        args.output_dir,
        output_path,
        report,
    )

    print(
        f"RAG eval: {passed}/{total} passed "
        f"({pass_rate:.1%}); report={output_path}"
    )
    print(f"CSV summary: {csv_path}")
    print(f"Markdown summary: {md_path}")
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(
            f"[{status}] {result['id']} hits={result['hit_count']} "
            f"time={result['elapsed_ms']}ms"
        )
        for failure in result["failures"]:
            print(f"  - {failure}")

    return 0 if pass_rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
