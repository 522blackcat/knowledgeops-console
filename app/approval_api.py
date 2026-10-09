"""
人工审批 API。

审批 API 只负责：
    - 读取待审批请求；
    - 记录批准或拒绝；
    - 通知 Worker 恢复任务。

审批 API 不直接执行工具。

只有原状态为 pending 的审批才能被处理，
防止重复审批覆盖先前决定。
"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from agent.events import (
    append_run_event,
    notify_agent_worker,
    notify_run_event,
)

from app.audit import write_audit_log

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from app.schemas import (
    ApprovalDecisionRequest,
    ApprovalResponse,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    AgentRun,
    ApprovalRequest,
    utc_now,
)


router = APIRouter(
    prefix="/api/approvals",
    tags=["人工审批"],
)


@router.get(
    "/pending",
    response_model=list[ApprovalResponse],
)
async def list_pending_approvals(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.APPROVAL_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """读取当前租户的待审批工具请求。"""

    result = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id
            == current_user.tenant_id,
            ApprovalRequest.status == "pending",
        )
        .order_by(
            ApprovalRequest.created_at.asc()
        )
        .limit(100)
    )

    return result.scalars().all()


@router.get(
    "/recent",
    response_model=list[ApprovalResponse],
)
async def list_recent_approvals(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.APPROVAL_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """读取当前租户最近的审批请求，包含已处理记录。"""

    result = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id
            == current_user.tenant_id,
        )
        .order_by(
            ApprovalRequest.created_at.desc()
        )
        .limit(100)
    )

    return result.scalars().all()


async def decide_approval(
    *,
    approval_id: uuid.UUID,
    decision: str,
    reason: str,
    current_user: CurrentUser,
    db: AsyncSession,
) -> ApprovalRequest:
    """
    在数据库事务内完成审批状态变更。

    通过行锁确保同一审批不会被并发覆盖。
    """

    approval = await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id
            == current_user.tenant_id,
        )
        .with_for_update()
    )

    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="审批请求不存在",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail="审批请求已经处理",
        )

    run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == approval.run_id,
            AgentRun.tenant_id
            == current_user.tenant_id,
        )
        .with_for_update()
    )

    if (
        run is None
        or run.status != "waiting_approval"
    ):
        raise HTTPException(
            status_code=409,
            detail="关联任务不处于待审批状态",
        )

    approval.status = decision
    approval.reviewed_by = current_user.id
    approval.review_reason = reason
    approval.reviewed_at = utc_now()
    approval_wait_ms = int(
        (
            approval.reviewed_at
            - approval.created_at
        ).total_seconds()
        * 1000
    )

    # 拒绝也需要恢复 LangGraph，
    # 由图中的审批节点决定后续行为。
    run.status = "queued"

    await append_run_event(
        db,
        tenant_id=current_user.tenant_id,
        run_id=run.id,
        event_type=(
            f"approval.{decision}"
        ),
        payload={
            "approval_id": str(
                approval.id
            ),
            "tool_name": approval.tool_name,
            "decision": decision,
            "approval_wait_ms": approval_wait_ms,
        },
    )

    await write_audit_log(
        db,
        current_user=current_user,
        action=f"approval.{decision}",
        resource_type="approval_request",
        resource_id=str(approval.id),
        summary=(
            f"{'批准' if decision == 'approved' else '拒绝'}"
            f"工具调用：{approval.tool_name}"
        ),
        metadata={
            "run_id": str(run.id),
            "tool_name": approval.tool_name,
            "decision": decision,
            "approval_wait_ms": approval_wait_ms,
            "reason": reason,
        },
    )

    await db.commit()
    await db.refresh(approval)

    try:
        await notify_agent_worker(
            run.id
        )

        await notify_run_event(
            run.id
        )

    except Exception:
        pass

    return approval


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalResponse,
)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.APPROVAL_REVIEW
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """批准指定工具调用。"""

    return await decide_approval(
        approval_id=approval_id,
        decision="approved",
        reason=body.reason,
        current_user=current_user,
        db=db,
    )


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalResponse,
)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.APPROVAL_REVIEW
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """拒绝指定工具调用。"""

    return await decide_approval(
        approval_id=approval_id,
        decision="rejected",
        reason=body.reason,
        current_user=current_user,
        db=db,
    )
