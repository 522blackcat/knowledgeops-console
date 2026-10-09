"""Check that documented Agent run events are present in code and docs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "RUN_EVENTS.md"
FRONTEND_PATH = ROOT / "frontend" / "src" / "App.vue"

BACKEND_PATHS = [
    ROOT / "app" / "approval_api.py",
    ROOT / "app" / "run_api.py",
    ROOT / "agent" / "worker.py",
    ROOT / "agent" / "completion.py",
]

REQUIRED_STAGES = {
    "queued",
    "started",
    "recovering",
    "retrieving",
    "generating",
    "low_confidence",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
}

REQUIRED_EVENT_TYPES = {
    "run.stage",
    "run.queued",
    "run.started",
    "rag.retrieved",
    "run.completed",
}

REQUIRED_FRONTEND_TITLES = {
    "任务已进入队列",
    "开始处理",
    "恢复运行",
    "检索知识库",
    "生成回答",
    "低置信度",
    "回答完成",
    "运行失败",
    "已取消",
    "运行超时",
}


def main() -> int:
    failures: list[str] = []

    doc = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
    frontend = FRONTEND_PATH.read_text(encoding="utf-8") if FRONTEND_PATH.exists() else ""
    backend = "\n".join(path.read_text(encoding="utf-8") for path in BACKEND_PATHS)

    for stage in sorted(REQUIRED_STAGES):
        if f"`{stage}`" not in doc:
            failures.append(f"stage {stage!r} missing from docs")
        if stage not in frontend:
            failures.append(f"stage {stage!r} missing from frontend")

    for stage in ["queued", "started", "recovering", "retrieving", "generating", "low_confidence", "completed"]:
        if f'"stage": "{stage}"' not in backend and stage not in backend:
            failures.append(f"stage {stage!r} missing from backend stage events")

    for status in ["failed", "cancelled", "timed_out"]:
        if status not in backend:
            failures.append(f"terminal status {status!r} missing from backend")

    for event_type in sorted(REQUIRED_EVENT_TYPES):
        if event_type not in backend:
            failures.append(f"event type {event_type!r} missing from backend")
        if event_type not in doc:
            failures.append(f"event type {event_type!r} missing from docs")

    for title in sorted(REQUIRED_FRONTEND_TITLES):
        if title not in frontend:
            failures.append(f"frontend title {title!r} missing from App.vue")
        if title not in doc:
            failures.append(f"frontend title {title!r} missing from docs")

    if "events.length&&busy" not in frontend:
        failures.append("run trace should be visible only while a run is busy")

    for marker in [
        "currentRunApproval",
        "inline-approval",
        "decide(currentRunApproval,'approve')",
        "decide(currentRunApproval,'reject')",
        "retrieval_stats",
        "检索耗时",
        "run_timing",
        "generation_ms",
        "total_run_ms",
        "queue_wait_ms",
        "recovery_lag_ms",
        "approval_wait_ms",
        "error_category",
        "error_category_label",
        "errorCategoryLabel",
        "approvalView",
        "/api/approvals/'+(approvalView.value==='recent'?'recent':'pending')",
        "最近记录",
        "approvalStatusType",
        "等待耗时",
        "排队等待",
        "恢复滞后",
    ]:
        if marker not in frontend:
            failures.append(f"inline approval marker missing: {marker}")

    for marker in [
        "classify_run_error",
        "ERROR_CATEGORY_LABELS",
        "error_category_label",
        "RUN_TIMEOUT",
        "run_timing",
        "generation_ms",
        "total_run_ms",
        "queue_wait_ms",
        "recovery_lag_ms",
        "approval_wait_ms",
        "error_category",
        "error_category_label",
        "rag_error",
        "model_error",
        "tool_error",
    ]:
        if marker not in backend:
            failures.append(f"backend failure marker missing: {marker}")

    for marker in [
        "run_timing",
        "generation_ms",
        "total_run_ms",
        "queue_wait_ms",
        "recovery_lag_ms",
        "approval_wait_ms",
    ]:
        if marker not in doc:
            failures.append(f"run timing marker missing from docs: {marker}")

    if failures:
        print("Run event check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "Run event check passed: "
        f"{len(REQUIRED_STAGES)} stages, "
        f"{len(REQUIRED_EVENT_TYPES)} event types."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
