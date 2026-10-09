"""Validate RAG eval cases and generated dashboard reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "evaluation" / "rag_eval_cases_v3.json"
RESULTS_DIR = ROOT / "evaluation" / "results"
API_PATH = ROOT / "app" / "rag_eval_api.py"
FRONTEND_PATH = ROOT / "frontend" / "src" / "App.vue"

MIN_CASES = 50
REQUIRED_SUMMARY_FIELDS = {
    "total",
    "passed",
    "failed",
    "pass_rate",
    "retrieval_mode",
    "use_reranker",
    "rank_metrics",
}
REQUIRED_RANK_METRICS = {
    "scored_cases",
    "hit@1",
    "hit@3",
    "hit@5",
    "mrr",
    "rank_histogram",
    "miss_ids",
}
REQUIRED_RESULT_FIELDS = {
    "id",
    "query",
    "passed",
    "failures",
    "elapsed_ms",
    "hit_count",
    "top_results",
}
REQUIRED_TOP_RESULT_FIELDS = {
    "filename",
    "score",
    "chunk_id",
    "preview",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_standard_report(payload: dict[str, Any]) -> bool:
    summary = payload.get("summary")
    results = payload.get("results")
    return isinstance(summary, dict) and isinstance(results, list)


def validate_cases(failures: list[str]) -> None:
    if not CASE_PATH.exists():
        failures.append(f"missing case file: {CASE_PATH}")
        return

    payload = load_json(CASE_PATH)
    cases = payload.get("cases")
    if not isinstance(cases, list):
        failures.append("case file must contain a cases array")
        return

    if len(cases) < MIN_CASES:
        failures.append(f"case count {len(cases)} < {MIN_CASES}")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or "")
        if not case_id:
            failures.append(f"case #{index} missing id")
        elif case_id in seen_ids:
            failures.append(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)

        if not str(case.get("query") or "").strip():
            failures.append(f"case {case_id or index} missing query")
        if not (
            case.get("anchor_text")
            or case.get("expected_terms")
            or case.get("expected_any_terms")
        ):
            failures.append(f"case {case_id or index} has no assertion fields")


def validate_report(path: Path, failures: list[str]) -> None:
    payload = load_json(path)
    if not is_standard_report(payload):
        return

    summary = payload["summary"]
    results = payload["results"]

    missing_summary = REQUIRED_SUMMARY_FIELDS - set(summary)
    if missing_summary:
        failures.append(
            f"{path.name} missing summary fields: "
            + ", ".join(sorted(missing_summary))
        )

    rank_metrics = summary.get("rank_metrics")
    if not isinstance(rank_metrics, dict):
        failures.append(f"{path.name} summary.rank_metrics must be an object")
        rank_metrics = {}

    missing_metrics = REQUIRED_RANK_METRICS - set(rank_metrics)
    if missing_metrics:
        failures.append(
            f"{path.name} missing rank_metrics fields: "
            + ", ".join(sorted(missing_metrics))
        )

    if summary.get("total") != len(results):
        failures.append(
            f"{path.name} summary.total {summary.get('total')} "
            f"!= results length {len(results)}"
        )

    for index, result in enumerate(results, start=1):
        result_id = str(result.get("id") or f"#{index}")
        missing_result = REQUIRED_RESULT_FIELDS - set(result)
        if missing_result:
            failures.append(
                f"{path.name} result {result_id} missing fields: "
                + ", ".join(sorted(missing_result))
            )
            continue

        if not isinstance(result.get("failures"), list):
            failures.append(f"{path.name} result {result_id} failures must be a list")
        if not isinstance(result.get("top_results"), list):
            failures.append(f"{path.name} result {result_id} top_results must be a list")
            continue

        top_results = result.get("top_results") or []
        if result.get("hit_count", 0) > 0 and not top_results:
            failures.append(f"{path.name} result {result_id} has hits but no top_results")

        for hit_index, hit in enumerate(top_results[:5], start=1):
            if not isinstance(hit, dict):
                failures.append(
                    f"{path.name} result {result_id} hit #{hit_index} is not an object"
                )
                continue
            missing_hit = REQUIRED_TOP_RESULT_FIELDS - set(hit)
            if missing_hit:
                failures.append(
                    f"{path.name} result {result_id} hit #{hit_index} missing fields: "
                    + ", ".join(sorted(missing_hit))
                )


def main() -> int:
    failures: list[str] = []
    validate_cases(failures)

    api_source = API_PATH.read_text(encoding="utf-8")
    frontend_source = FRONTEND_PATH.read_text(encoding="utf-8")
    for marker in [
        "comparison",
        "previous_file",
        "metric_delta",
        "/eval/case",
        "/eval/retest",
        "classify_eval_failures",
        "write_batch_retest_report",
        "write_retest_report",
        "retest_history",
        "report_type",
        "case_id",
        "trend_point",
        "aggregate_keys",
        "fail_rate",
        "examples",
        "\"trend\"",
    ]:
        if marker not in api_source:
            failures.append(f"eval API missing report comparison marker: {marker}")
    for marker in [
        "evalDelta",
        "evalFailureReason",
        "evalAggregateFilter",
        "toggleAggregateFilter",
        "aggregateMeta",
        "对比对象",
        "failure_groups",
        "failure_categories",
        "retestEvalCase",
        "retestVisibleEvalCases",
        "批量复测当前筛选",
        "evalTrend",
        "evalRetestHistory",
        "reportTypeLabel",
        "trendBars",
        "eval-trend",
        "eval-retest",
        "eval-retest-history",
        "top_results",
    ]:
        if marker not in frontend_source:
            failures.append(f"eval dashboard missing marker: {marker}")

    if not RESULTS_DIR.exists():
        failures.append(f"missing results directory: {RESULTS_DIR}")
    else:
        report_count = 0
        for path in sorted(RESULTS_DIR.glob("*.json")):
            payload = load_json(path)
            if not is_standard_report(payload):
                continue
            report_count += 1
            validate_report(path, failures)
        if report_count == 0:
            failures.append("no standard eval reports found in evaluation/results")

    if failures:
        print("Eval report check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Eval report check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
