"""RAG 检索自测 API。"""

import json
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path

import httpx

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from infrastructure.database import (
    get_db,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.models import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)

from rag.bm25 import (
    bm25_search,
)

from evaluation.rag_eval_runner import (
    judge_case,
)


router = APIRouter(
    prefix="/api/rag",
    tags=["RAG 自测"],
)


class RagEvalRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=2000,
    )
    knowledge_scope: str = "global"
    knowledge_base_ids: list[uuid.UUID] = Field(
        default_factory=list
    )
    retrieval_mode: str = "lexical"
    use_reranker: bool = False


class RagEvalCaseRequest(BaseModel):
    case: dict
    retrieval_mode: str = "lexical"
    use_reranker: bool = False


class RagEvalBatchRetestRequest(BaseModel):
    cases: list[dict] = Field(
        min_length=1,
        max_length=100,
    )
    retrieval_mode: str = "lexical"
    use_reranker: bool = False


def classify_eval_failures(
    failures: list[str],
    *,
    hit_count: int | None = None,
    rank: int | None = None,
) -> list[str]:
    """Map free-form eval failures to stable dashboard categories."""

    categories: set[str] = set()

    if not failures:
        return []

    if hit_count is not None and hit_count <= 0:
        categories.add("召回为空")

    if rank is None and hit_count:
        categories.add("锚点未命中")
    elif rank is not None and rank > 3:
        categories.add("排序靠后")

    for failure in failures:
        text = str(failure)
        if text.startswith("hit_count"):
            categories.add("召回不足")
        elif "expected_terms" in text or "anchor" in text:
            categories.add("锚点未命中")
        elif "expected_any_terms" in text:
            categories.add("同义/关键词未命中")
        elif "expected_top_any_terms" in text:
            categories.add("Top 结果不准")
        elif "forbidden_top_terms" in text:
            categories.add("Top 结果污染")
        elif "expected_filenames" in text:
            categories.add("文件来源不符")
        elif "Timeout" in text or "ReadTimeout" in text:
            categories.add("接口超时")
        elif "HTTP" in text or "Error" in text:
            categories.add("接口异常")

    return sorted(categories or {"其它"})


def chunk_section_label(
    metadata: dict,
) -> str | None:
    """Return a readable non-page location for eval evidence."""

    heading = str(
        metadata.get("heading") or ""
    ).strip()
    if heading:
        return heading

    sheet = str(
        metadata.get("sheet") or ""
    ).strip()
    if sheet:
        return f"工作表：{sheet}"

    return None


def utc_timestamp() -> str:
    """Return a compact UTC timestamp for eval artifact filenames."""

    return datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def write_retest_report(
    *,
    case: dict,
    result: dict,
    retrieval_mode: str | None,
    use_reranker: bool | None,
) -> str:
    """Persist a single-case retest as a normal eval report."""

    results_dir = Path("evaluation/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    case_id = str(case.get("id") or "case")
    safe_case_id = "".join(
        char
        if char.isalnum() or char in {"-", "_"}
        else "_"
        for char in case_id
    )[:80]
    filename = (
        f"retest_{safe_case_id}_{utc_timestamp()}.json"
    )
    path = results_dir / filename

    passed = bool(result.get("passed"))
    payload = {
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "report_type": "single_case_retest",
        "case_id": case_id,
        "summary": {
            "total": 1,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
            "pass_rate": 1.0 if passed else 0.0,
            "retrieval_mode": retrieval_mode,
            "use_reranker": bool(use_reranker),
            "rank_metrics": {
                "scored_cases": 1,
                "hit@1": 1.0
                if result.get("anchor_rank") == 1
                else 0.0,
                "hit@3": 1.0
                if (
                    result.get("anchor_rank")
                    and result.get("anchor_rank") <= 3
                )
                else 0.0,
                "hit@5": 1.0
                if (
                    result.get("anchor_rank")
                    and result.get("anchor_rank") <= 5
                )
                else 0.0,
                "mrr": (
                    1.0 / result.get("anchor_rank")
                    if result.get("anchor_rank")
                    else 0.0
                ),
                "rank_histogram": {
                    str(result.get("anchor_rank")): 1
                }
                if result.get("anchor_rank")
                else {},
                "miss_ids": []
                if result.get("anchor_rank")
                else [case_id],
            },
        },
        "results": [result],
    }

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return filename


def build_retest_summary(
    *,
    results: list[dict],
    retrieval_mode: str | None,
    use_reranker: bool | None,
) -> dict:
    """Build dashboard-compatible summary for retest results."""

    total = len(results)
    passed = sum(
        1
        for item in results
        if item.get("passed")
    )
    ranks = [
        int(item["anchor_rank"])
        for item in results
        if item.get("anchor_rank")
    ]
    rank_histogram: dict[str, int] = {}
    for rank in ranks:
        key = str(rank)
        rank_histogram[key] = (
            rank_histogram.get(key, 0) + 1
        )

    scored = len(results)

    def hit_at(k: int) -> float:
        if not scored:
            return 0.0
        return sum(
            1
            for rank in ranks
            if rank <= k
        ) / scored

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (
            passed / total
            if total
            else 0.0
        ),
        "retrieval_mode": retrieval_mode,
        "use_reranker": bool(use_reranker),
        "rank_metrics": {
            "scored_cases": scored,
            "hit@1": hit_at(1),
            "hit@3": hit_at(3),
            "hit@5": hit_at(5),
            "mrr": (
                sum(1.0 / rank for rank in ranks)
                / scored
                if scored
                else 0.0
            ),
            "rank_histogram": rank_histogram,
            "miss_ids": [
                str(item.get("id"))
                for item in results
                if not item.get("anchor_rank")
            ],
        },
    }


def write_batch_retest_report(
    *,
    results: list[dict],
    retrieval_mode: str | None,
    use_reranker: bool | None,
) -> str:
    """Persist a batch retest report."""

    results_dir = Path("evaluation/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    filename = f"retest_batch_{utc_timestamp()}.json"
    path = results_dir / filename
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "created_at": now,
        "generated_at": now,
        "report_type": "batch_retest",
        "summary": build_retest_summary(
            results=results,
            retrieval_mode=retrieval_mode,
            use_reranker=use_reranker,
        ),
        "results": results,
    }
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return filename


def load_eval_case_index() -> dict[str, dict]:
    """Load JSON eval cases by id for dashboard retest actions."""

    case_path = Path(
        "evaluation/rag_eval_cases_v3.json"
    )
    try:
        payload = json.loads(
            case_path.read_text(encoding="utf-8")
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {}

    cases = payload.get("cases") or []
    if not isinstance(cases, list):
        return {}

    return {
        str(item.get("id")): item
        for item in cases
        if isinstance(item, dict) and item.get("id")
    }


@router.get("/eval/reports")
async def list_rag_eval_reports(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
):
    """返回最近的 RAG 评测报告摘要。"""

    del current_user

    results_dir = Path(
        "evaluation/results"
    )

    if not results_dir.exists():
        return {
            "reports": [],
        }

    case_index = load_eval_case_index()

    parsed_reports = []
    for path in sorted(
        results_dir.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:20]:
        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ):
            continue

        summary = payload.get("summary") or {}
        results = payload.get("results") or []

        if not {
            "total",
            "passed",
            "failed",
            "pass_rate",
        }.issubset(summary):
            continue

        if not isinstance(results, list):
            continue
        parsed_reports.append(
            (
                path,
                payload,
                summary,
                results,
            )
        )

    def metric_value(
        summary: dict,
        key: str,
    ) -> float | None:
        """Read a report metric as a number when possible."""

        rank_metrics = (
            summary.get("rank_metrics") or {}
        )
        value = (
            summary.get(key)
            if key in summary
            else rank_metrics.get(key)
        )
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def metric_delta(
        current: dict,
        previous: dict | None,
        key: str,
    ) -> dict:
        """Return current metric, previous metric and delta."""

        current_value = metric_value(
            current,
            key,
        )
        previous_value = (
            metric_value(previous, key)
            if previous
            else None
        )
        delta = (
            current_value - previous_value
            if current_value is not None
            and previous_value is not None
            else None
        )
        return {
            "current": current_value,
            "previous": previous_value,
            "delta": delta,
        }

    def trend_point(
        path: Path,
        payload: dict,
        summary: dict,
    ) -> dict:
        """Return compact trend metrics for the dashboard."""

        return {
            "file": path.name,
            "generated_at": payload.get(
                "generated_at"
            )
            or payload.get("created_at"),
            "report_type": payload.get(
                "report_type",
                "eval",
            ),
            "retrieval_mode": summary.get(
                "retrieval_mode"
            ),
            "use_reranker": summary.get(
                "use_reranker"
            ),
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
            "pass_rate": metric_value(
                summary,
                "pass_rate",
            ),
            "hit@1": metric_value(
                summary,
                "hit@1",
            ),
            "hit@3": metric_value(
                summary,
                "hit@3",
            ),
            "hit@5": metric_value(
                summary,
                "hit@5",
            ),
            "mrr": metric_value(
                summary,
                "mrr",
            ),
        }

    trend = [
        trend_point(
            path,
            payload,
            summary,
        )
        for path, payload, summary, _ in reversed(
            parsed_reports[:12]
        )
    ]

    reports = []
    retest_history = [
        {
            "file": path.name,
            "generated_at": payload.get(
                "generated_at"
            )
            or payload.get("created_at"),
            "report_type": payload.get(
                "report_type"
            ),
            "case_id": payload.get(
                "case_id"
            ),
            "summary": summary,
        }
        for path, payload, summary, _ in parsed_reports
        if str(
            payload.get("report_type", "")
        ).endswith("retest")
        or str(
            payload.get("report_type", "")
        ).startswith("single_case_retest")
    ][:8]

    for index, (
        path,
        payload,
        summary,
        results,
    ) in enumerate(parsed_reports):
        previous_summary = (
            parsed_reports[index + 1][2]
            if index + 1 < len(parsed_reports)
            else None
        )

        failures = [
            item
            for item in results
            if not item.get("passed")
        ][:10]
        failure_groups: dict[str, int] = {}
        for item in results:
            for reason in item.get("failures") or []:
                failure_groups[reason] = (
                    failure_groups.get(reason, 0)
                    + 1
                )

        def first_hit(item: dict) -> dict:
            """Return the top retrieval hit from a runner result."""

            top_results = item.get("top_results")
            if (
                isinstance(top_results, list)
                and top_results
            ):
                return top_results[0] or {}
            return item.get("top_hit") or {}

        def top_hits(item: dict) -> list[dict]:
            """Return compact retrieval evidence for dashboard drill-down."""

            hits = item.get("top_results")
            if not isinstance(hits, list):
                hit = item.get("top_hit")
                hits = [hit] if isinstance(hit, dict) else []
            compact_hits = []
            for hit in hits[:5]:
                if not isinstance(hit, dict):
                    continue
                text = str(
                    hit.get("preview")
                    or hit.get("text")
                    or ""
                )
                compact_hits.append({
                    "filename": hit.get("filename"),
                    "knowledge_base_name": hit.get(
                        "knowledge_base_name"
                    ),
                    "knowledge_base_scope": hit.get(
                        "knowledge_base_scope"
                    ),
                    "chunk_id": hit.get("chunk_id"),
                    "document_version": hit.get(
                        "document_version"
                    ),
                    "current_version": hit.get(
                        "current_version"
                    ),
                    "section_label": hit.get(
                        "section_label"
                    )
                    or chunk_section_label(hit),
                    "heading": hit.get("heading"),
                    "sheet": hit.get("sheet"),
                    "source_page": hit.get("source_page"),
                    "score": hit.get("score"),
                    "preview": (
                        text[:220] + "..."
                        if len(text) > 220
                        else text
                    ),
                })
            return compact_hits

        def aggregate_keys(item: dict) -> dict:
            """Return dashboard grouping keys for one eval case."""

            hit = first_hit(item)
            categories = classify_eval_failures(
                item.get("failures") or [],
                hit_count=item.get("hit_count"),
                rank=item.get("anchor_rank"),
            )
            return {
                "scope": str(
                    hit.get("knowledge_base_scope")
                    or "未记录"
                ),
                "file": str(
                    hit.get("filename") or "未记录"
                ),
                "category": categories or ["其它"],
            }

        def build_failure_aggregates(
            all_results: list[dict],
        ) -> dict:
            """Group failed eval cases by KB scope, file and failure category."""

            aggregates: dict[
                str,
                dict[str, dict],
            ] = {
                "scope": {},
                "file": {},
                "category": {},
            }

            def ensure(
                group: str,
                key: object,
            ) -> dict:
                label = str(key or "未记录")
                if label not in aggregates[group]:
                    aggregates[group][label] = {
                        "name": label,
                        "count": 0,
                        "total": 0,
                        "examples": [],
                    }
                return aggregates[group][label]

            for item in all_results:
                keys = aggregate_keys(item)
                groups = {
                    "scope": [keys["scope"]],
                    "file": [keys["file"]],
                    "category": keys["category"],
                }
                for group, values in groups.items():
                    for value in values:
                        entry = ensure(group, value)
                        entry["total"] += 1
                        if not item.get("passed"):
                            entry["count"] += 1
                            if len(entry["examples"]) < 5:
                                entry["examples"].append(
                                    str(item.get("id"))
                                )

            return {
                name: [
                    {
                        **entry,
                        "fail_rate": (
                            entry["count"]
                            / entry["total"]
                            if entry["total"]
                            else 0
                        ),
                    }
                    for entry in sorted(
                        values.values(),
                        key=lambda item: (
                            item["count"],
                            (
                                item["count"]
                                / item["total"]
                                if item["total"]
                                else 0
                            ),
                        ),
                        reverse=True,
                    )
                    if entry["count"] > 0
                ][:10]
                for name, values in aggregates.items()
            }

        cases = [
            {
                **(
                    case_index.get(
                        str(item.get("id"))
                    )
                    or {}
                ),
                "id": item.get("id"),
                "query": item.get("query"),
                "passed": bool(
                    item.get("passed")
                ),
                "rank": item.get(
                    "anchor_rank"
                ),
                "hit_count": item.get(
                    "hit_count"
                ),
                "elapsed_ms": item.get(
                    "elapsed_ms"
                ),
                "failures": item.get(
                    "failures",
                    [],
                ),
                "failure_categories": (
                    classify_eval_failures(
                        item.get("failures") or [],
                        hit_count=item.get("hit_count"),
                        rank=item.get("anchor_rank"),
                    )
                ),
                "failure_aggregate_keys": (
                    aggregate_keys(item)
                ),
                "top_scope": first_hit(item).get(
                    "knowledge_base_scope"
                ),
                "top_file": first_hit(item).get(
                    "filename"
                ),
                "top_score": first_hit(item).get(
                    "score"
                ),
                "top_chunk_id": first_hit(item).get(
                    "chunk_id"
                ),
                "top_results": top_hits(item),
            }
            for item in results
        ]

        reports.append({
            "file": path.name,
            "generated_at": payload.get(
                "generated_at"
            ),
            "report_type": payload.get(
                "report_type",
                "eval",
            ),
            "case_id": payload.get("case_id"),
            "summary": summary,
            "comparison": {
                "previous_file": (
                    parsed_reports[index + 1][0].name
                    if index + 1 < len(parsed_reports)
                    else None
                ),
                "pass_rate": metric_delta(
                    summary,
                    previous_summary,
                    "pass_rate",
                ),
                "hit@1": metric_delta(
                    summary,
                    previous_summary,
                    "hit@1",
                ),
                "hit@3": metric_delta(
                    summary,
                    previous_summary,
                    "hit@3",
                ),
                "hit@5": metric_delta(
                    summary,
                    previous_summary,
                    "hit@5",
                ),
                "mrr": metric_delta(
                    summary,
                    previous_summary,
                    "mrr",
                ),
            },
            "rank_histogram": (
                summary.get("rank_metrics", {})
                .get("rank_histogram", {})
            ),
            "failure_groups": [
                {
                    "reason": reason,
                    "count": count,
                }
                for reason, count in sorted(
                    failure_groups.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            ],
            "failure_aggregates": (
                build_failure_aggregates(
                    results
                )
            ),
            "metric_notes": {
                "pass_rate": (
                    "通过用例数 / 总用例数。"
                    "用例是否通过由 JSON case 中的命中数量、"
                    "required_terms、anchor_text 等断言决定。"
                ),
                "hit@1": (
                    "标准答案锚点出现在第 1 个返回片段的比例。"
                ),
                "hit@3": (
                    "标准答案锚点出现在前 3 个返回片段的比例。"
                ),
                "hit@5": (
                    "标准答案锚点出现在前 5 个返回片段的比例。"
                ),
                "mrr": (
                    "Mean Reciprocal Rank，"
                    "标准答案排名越靠前分数越高。"
                ),
            },
            "failures": [
                {
                    **(
                        case_index.get(
                            str(item.get("id"))
                        )
                        or {}
                    ),
                    "id": item.get("id"),
                    "query": item.get("query"),
                    "rank": item.get(
                        "anchor_rank"
                    ),
                    "hit_count": item.get(
                        "hit_count"
                    ),
                    "failures": item.get(
                        "failures",
                        [],
                    ),
                    "failure_categories": (
                        classify_eval_failures(
                            item.get("failures") or [],
                            hit_count=item.get("hit_count"),
                            rank=item.get("anchor_rank"),
                        )
                    ),
                    "top_file": (
                        first_hit(item).get(
                            "filename"
                        )
                    ),
                    "top_score": first_hit(item).get(
                        "score"
                    ),
                    "top_chunk_id": first_hit(item).get(
                        "chunk_id"
                    ),
                    "top_results": top_hits(item),
                }
                for item in failures
            ],
            "cases": cases,
        })

    return {
        "reports": reports,
        "trend": trend,
        "retest_history": retest_history,
    }


@router.post("/eval/case")
async def evaluate_rag_case(
    body: RagEvalCaseRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """用在线检索接口复测单条 eval case。"""

    started = time.perf_counter()
    response = await evaluate_rag(
        RagEvalRequest(
            query=str(body.case.get("query") or ""),
            knowledge_scope=body.case.get(
                "knowledge_scope",
                "global",
            ),
            knowledge_base_ids=body.case.get(
                "knowledge_base_ids",
                [],
            ),
            retrieval_mode=body.case.get(
                "retrieval_mode",
                body.retrieval_mode,
            ),
            use_reranker=bool(
                body.case.get(
                    "use_reranker",
                    body.use_reranker,
                )
            ),
        ),
        current_user=current_user,
        db=db,
    )
    elapsed_ms = int(
        (time.perf_counter() - started) * 1000
    )
    result = judge_case(
        body.case,
        response,
        elapsed_ms,
    )
    result["failure_categories"] = classify_eval_failures(
        result.get("failures") or [],
        hit_count=result.get("hit_count"),
        rank=result.get("anchor_rank"),
    )
    result["retrieval_mode"] = response.get(
        "retrieval_mode"
    )
    result["use_reranker"] = response.get(
        "use_reranker"
    )
    result["retest_report"] = write_retest_report(
        case=body.case,
        result=result,
        retrieval_mode=result.get(
            "retrieval_mode"
        ),
        use_reranker=result.get(
            "use_reranker"
        ),
    )
    return result


@router.post("/eval/retest")
async def evaluate_rag_batch_retest(
    body: RagEvalBatchRetestRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """批量复测 eval cases，并保存一份独立报告。"""

    results = []
    started = time.perf_counter()

    for case in body.cases:
        case_started = time.perf_counter()
        response = await evaluate_rag(
            RagEvalRequest(
                query=str(case.get("query") or ""),
                knowledge_scope=case.get(
                    "knowledge_scope",
                    "global",
                ),
                knowledge_base_ids=case.get(
                    "knowledge_base_ids",
                    [],
                ),
                retrieval_mode=case.get(
                    "retrieval_mode",
                    body.retrieval_mode,
                ),
                use_reranker=bool(
                    case.get(
                        "use_reranker",
                        body.use_reranker,
                    )
                ),
            ),
            current_user=current_user,
            db=db,
        )
        elapsed_ms = int(
            (
                time.perf_counter()
                - case_started
            )
            * 1000
        )
        result = judge_case(
            case,
            response,
            elapsed_ms,
        )
        result["failure_categories"] = classify_eval_failures(
            result.get("failures") or [],
            hit_count=result.get("hit_count"),
            rank=result.get("anchor_rank"),
        )
        result["retrieval_mode"] = response.get(
            "retrieval_mode"
        )
        result["use_reranker"] = response.get(
            "use_reranker"
        )
        results.append(result)

    report = write_batch_retest_report(
        results=results,
        retrieval_mode=body.retrieval_mode,
        use_reranker=body.use_reranker,
    )
    summary = build_retest_summary(
        results=results,
        retrieval_mode=body.retrieval_mode,
        use_reranker=body.use_reranker,
    )
    return {
        "report": report,
        "summary": summary,
        "elapsed_ms": int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        ),
        "results": results,
    }


@router.post("/eval")
async def evaluate_rag(
    body: RagEvalRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.KNOWLEDGE_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """直接返回检索命中，用于验证知识库是否可用。"""

    if body.knowledge_scope == "custom":
        knowledge_base_ids = body.knowledge_base_ids
    else:
        result = await db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.tenant_id
                == current_user.tenant_id
            )
        )
        knowledge_base_ids = list(
            result.scalars().all()
        )

    results = []

    settings = get_settings()

    for knowledge_base_id in knowledge_base_ids:
        knowledge_base = await db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.tenant_id
                == current_user.tenant_id,
            )
        )

        if knowledge_base is None:
            continue

        if body.retrieval_mode == "lexical":
            bm25_hits = await bm25_search(
                db,
                tenant_id=current_user.tenant_id,
                knowledge_base_id=knowledge_base.id,
                query=body.query,
                limit=10,
            )
            chunk_ids = [
                hit.chunk_id
                for hit in bm25_hits
            ]
            chunk_scores = {
                hit.chunk_id: hit.score
                for hit in bm25_hits
            }
            if not chunk_ids:
                hits = []
            else:
                rows = await db.execute(
                    select(
                        DocumentChunk,
                        KnowledgeDocument,
                    )
                    .join(
                        KnowledgeDocument,
                        DocumentChunk.document_id
                        == KnowledgeDocument.id,
                    )
                    .where(
                        DocumentChunk.id.in_(chunk_ids),
                        DocumentChunk.tenant_id
                        == current_user.tenant_id,
                        KnowledgeDocument.tenant_id
                        == current_user.tenant_id,
                        KnowledgeDocument.knowledge_base_id
                        == knowledge_base.id,
                        KnowledgeDocument.status == "ready",
                        KnowledgeDocument.deleted_at.is_(None),
                        DocumentChunk.document_version
                        == KnowledgeDocument.current_version,
                    )
                )
                row_map = {
                    chunk.id: (
                        chunk,
                        document,
                    )
                    for chunk, document in rows.all()
                }
                hits = [
                    {
                        "chunk_id": chunk.id,
                        "document_id": document.id,
                        "text": chunk.text,
                        "source_page": chunk.source_page,
                        "score": chunk_scores.get(
                            chunk.id,
                            0.0,
                        ),
                        "metadata": {
                            "knowledge_base_id": str(
                                knowledge_base.id
                            ),
                            "knowledge_base_name": (
                                knowledge_base.name
                            ),
                            "knowledge_base_scope": (
                                knowledge_base.scope
                            ),
                            "filename": document.filename,
                            "external_id": document.external_id,
                            "document_version": (
                                chunk.document_version
                            ),
                            "current_version": (
                                document.current_version
                            ),
                            **chunk.metadata_json,
                        },
                    }
                    for chunk_id in chunk_ids
                    if chunk_id in row_map
                    for chunk, document in [
                        row_map[chunk_id]
                    ]
                ]
        else:
            if settings.rag_retrieval_url:
                try:
                    async with httpx.AsyncClient(
                        timeout=settings.qdrant_timeout_seconds
                    ) as client:
                        response = await client.post(
                            (
                                settings.rag_retrieval_url.rstrip("/")
                                + "/internal/rag/retrieve"
                            ),
                            json={
                                "tenant_id": str(
                                    current_user.tenant_id
                                ),
                                "knowledge_base_ids": [
                                    str(knowledge_base.id)
                                ],
                                "query": body.query,
                                "use_reranker": body.use_reranker,
                            },
                        )
                        response.raise_for_status()
                        payload = response.json()

                except (
                    httpx.HTTPError,
                    ValueError,
                ) as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "RAG 检索服务暂时不可用："
                            f"{exc}"
                        ),
                    ) from exc

                hits = []
                for item in payload.get("citations") or []:
                    hits.append({
                        "chunk_id": item.get(
                            "chunk_id"
                        ),
                        "document_id": item.get(
                            "document_id"
                        ),
                        "text": item.get(
                            "preview",
                            "",
                        ),
                        "source_page": item.get(
                            "source_page"
                        ),
                        "score": item.get("score"),
                        "metadata": {
                            "knowledge_base_id": (
                                item.get(
                                    "knowledge_base_id"
                                )
                            ),
                            "knowledge_base_name": (
                                item.get(
                                    "knowledge_base_name"
                                )
                            ),
                            "knowledge_base_scope": (
                                item.get(
                                    "knowledge_base_scope"
                                )
                            ),
                            "filename": item.get(
                                "filename"
                            ),
                            "external_id": item.get(
                                "external_id"
                            ),
                            "document_version": (
                                item.get(
                                    "document_version"
                                )
                            ),
                            "current_version": (
                                item.get(
                                    "current_version"
                                )
                            ),
                            "heading": item.get(
                                "heading"
                            ),
                            "sheet": item.get(
                                "sheet"
                            ),
                            "section_label": (
                                item.get(
                                    "section_label"
                                )
                            ),
                        },
                    })
            else:
                from rag.retrieval import (
                    hybrid_retrieve,
                )

                hits = await hybrid_retrieve(
                    db,
                    tenant_id=current_user.tenant_id,
                    knowledge_base_id=knowledge_base.id,
                    query=body.query,
                    use_reranker=body.use_reranker,
                )

        for hit in hits[
            : settings.rag_display_top_k
        ]:
            if isinstance(hit, dict):
                hit_data = hit
                text = str(
                    hit_data.get("text") or ""
                ).strip()
                metadata = hit_data.get("metadata") or {}
            else:
                hit_data = {
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.document_id,
                    "source_page": hit.source_page,
                    "score": hit.score,
                }
                text = str(hit.text or "").strip()
                metadata = hit.metadata
            results.append({
                "knowledge_base_id": str(
                    knowledge_base.id
                ),
                "knowledge_base_name": (
                    knowledge_base.name
                ),
                "knowledge_base_scope": (
                    metadata.get("knowledge_base_scope")
                    or knowledge_base.scope
                ),
                "chunk_id": str(hit_data["chunk_id"]),
                "document_id": str(hit_data["document_id"]),
                "document_version": metadata.get(
                    "document_version"
                ),
                "current_version": metadata.get(
                    "current_version"
                ),
                "section_label": (
                    metadata.get("section_label")
                    or chunk_section_label(metadata)
                ),
                "heading": metadata.get("heading"),
                "sheet": metadata.get("sheet"),
                "filename": metadata.get(
                    "filename",
                    "知识库文档",
                ),
                "source_page": hit_data["source_page"],
                "score": hit_data["score"],
                "text": text,
                "preview": (
                    text[:260] + "..."
                    if len(text) > 260
                    else text
                ),
            })

    results.sort(
        key=lambda item: float(
            item.get("score") or 0
        ),
        reverse=True,
    )

    return {
        "query": body.query,
        "retrieval_mode": body.retrieval_mode,
        "use_reranker": body.use_reranker,
        "hit_count": len(results),
        "results": results[
            : settings.rag_display_top_k
        ],
    }
