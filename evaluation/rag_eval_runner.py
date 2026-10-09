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


DEFAULT_CASE_FILE = Path(__file__).with_name("rag_eval_cases_v3.json")
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


def required_terms(case: dict[str, Any]) -> list[str]:
    """判案必须命中的词：v2 的 expected_terms 加 v3 的 anchor_text。

    v3 用例不再手写期望词，改用 anchor_text 记录原文里的答案行。
    锚点同样必须被召回，否则判案会退化成"只要有结果就算过"。
    """

    terms = [
        str(item).strip()
        for item in case.get("expected_terms", [])
        if str(item).strip()
    ]
    anchor = str(case.get("anchor_text") or "").strip()
    if anchor and anchor not in terms:
        terms.append(anchor)
    return terms


def anchor_rank(
    results: list[dict[str, Any]],
    terms: list[str],
) -> int | None:
    """第一条含任一判案词的命中排在第几；没进返回列表就是 None。

    v3 用例的判案词就是锚点本身，所以这个名次即"答案行被排到第几"。
    """

    for index, item in enumerate(results, start=1):
        text = str(item.get("text") or item.get("preview") or "")
        if any(term in text for term in terms):
            return index
    return None


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
    expected_terms = required_terms(case)
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
        "anchor_rank": anchor_rank(results, expected_terms),
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


def rank_metrics(
    results: list[dict[str, Any]],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    """把 pass_rate 拆回 hit@k / MRR。

    pass_rate 判的是"锚点在返回的整页结果里"，等价 hit@返回条数，
    只看它会把首条精度完全藏住——而首条精度才是分块和融合调参的目标。
    """

    scored = len(results)
    if scored == 0:
        return {"scored_cases": 0}

    hits = {f"hit@{k}": 0 for k in ks}
    reciprocal_rank = 0.0
    rank_histogram: dict[str, int] = {}
    miss_ids: list[str] = []
    for result in results:
        rank = result.get("anchor_rank")
        if rank is None:
            miss_ids.append(str(result["id"]))
            continue
        rank_histogram[str(rank)] = rank_histogram.get(str(rank), 0) + 1
        reciprocal_rank += 1.0 / rank
        for k in ks:
            if rank <= k:
                hits[f"hit@{k}"] += 1

    return {
        "scored_cases": scored,
        **{
            name: round(value / scored, 4)
            for name, value in hits.items()
        },
        "mrr": round(reciprocal_rank / scored, 4),
        "rank_histogram": dict(sorted(rank_histogram.items())),
        "miss_ids": miss_ids,
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
        "anchor_rank",
        "hit_count",
        "elapsed_ms",
        "query",
        "top_filename",
        "top_section",
        "top_page",
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
                    "anchor_rank": result.get("anchor_rank") or "",
                    "hit_count": result["hit_count"],
                    "elapsed_ms": result["elapsed_ms"],
                    "query": result.get("query") or "",
                    "top_filename": first_result_value(result, "filename"),
                    "top_section": first_result_value(result, "section_label"),
                    "top_page": first_result_value(result, "source_page"),
                    "top_score": first_result_value(result, "score"),
                    "failures": "; ".join(result.get("failures") or []),
                }
            )

    summary = report["summary"]
    metrics = summary.get("rank_metrics") or {}
    lines = [
        "# RAG Eval Summary",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- Retrieval mode: {summary['retrieval_mode']}",
        f"- Use reranker: {summary['use_reranker']}",
    ]
    if metrics.get("scored_cases"):
        lines += [
            f"- Scored cases: {metrics['scored_cases']}",
            f"- hit@1: {metrics.get('hit@1', 0):.4f}",
            f"- hit@3: {metrics.get('hit@3', 0):.4f}",
            f"- hit@5: {metrics.get('hit@5', 0):.4f}",
            f"- MRR: {metrics['mrr']:.4f}",
            f"- Rank histogram: {metrics.get('rank_histogram') or {}}",
            f"- Miss ids: {', '.join(metrics.get('miss_ids') or []) or '(none)'}",
        ]
    lines += [
        "",
        "| ID | Result | Rank | Hits | Time | Query | Top file | Failures |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for result in rows:
        status = "PASS" if result["passed"] else "FAIL"
        query = str(result.get("query") or "").replace("|", "\\|")
        top_filename = first_result_value(result, "filename").replace("|", "\\|")
        top_section = first_result_value(result, "section_label").replace("|", "\\|")
        top_page = first_result_value(result, "source_page")
        if top_section:
            top_filename = f"{top_filename} / {top_section}"
        if top_page:
            top_filename = f"{top_filename} 第{top_page}页"
        failures = "; ".join(result.get("failures") or []).replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result["id"]),
                    status,
                    str(result.get("anchor_rank") or "-"),
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
            "rank_metrics": rank_metrics(results),
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
    metrics = report["summary"]["rank_metrics"]
    if metrics.get("scored_cases"):
        print(
            "rank metrics: "
            + " ".join(
                f"{name}={metrics[name]}"
                for name in ("hit@1", "hit@3", "hit@5", "mrr")
            )
            + f" miss={len(metrics.get('miss_ids') or [])}"
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
