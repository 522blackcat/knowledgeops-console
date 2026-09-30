"""Agent 运行任务的创建、查询和取消。"""

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

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from app.schemas import (
    RunCreateRequest,
    RunResponse,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    AgentDefinition,
    AgentRun,
    Conversation,
)

from infrastructure.redis_client import (
    get_redis,
)


router = APIRouter(
    prefix="/api/runs",
    tags=["Agent 运行"],
)


TERMINAL_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
    "timed_out",
})


@router.post(
    "",
    response_model=RunResponse,
    status_code=202,
)
async def create_run(
    body: RunCreateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_CREATE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    创建 Agent 任务。

    不在 HTTP 请求中执行模型或工具。
    Worker 从 PostgreSQL 领取任务。
    """

    settings = get_settings()

    if len(body.question) > (
        settings.max_question_length
    ):
        raise HTTPException(
            status_code=422,
            detail="问题长度超过限制",
        )

    agent = await db.scalar(
        select(AgentDefinition).where(
            AgentDefinition.id == body.agent_id,
            AgentDefinition.tenant_id
            == current_user.tenant_id,
            AgentDefinition.status != "disabled",
        )
    )

    if agent is None:
        raise HTTPException(
            status_code=404,
            detail="Agent 不存在或不可用",
        )

    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.tenant_id
            == current_user.tenant_id,
            Conversation.user_id
            == current_user.id,
            Conversation.agent_id == agent.id,
            Conversation.status == "active",
        )
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前 Agent",
        )

    if body.idempotency_key:
        existing = await db.scalar(
            select(AgentRun).where(
                AgentRun.tenant_id
                == current_user.tenant_id,
                AgentRun.idempotency_key
                == body.idempotency_key,
            )
        )

        if existing is not None:
            if (
                existing.user_id
                != current_user.id
                or existing.agent_id
                != body.agent_id
                or existing.conversation_id
                != body.conversation_id
                or existing.question
                != body.question
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "幂等键已用于其他请求"
                    ),
                )

            return existing

    run_id = uuid.uuid4()

    run = AgentRun(
        id=run_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        status="queued",
        question=body.question,
        checkpoint_thread_id=str(run_id),
        idempotency_key=body.idempotency_key,
    )

    db.add(run)

    await append_run_event(
        db,
        tenant_id=current_user.tenant_id,
        run_id=run_id,
        event_type="run.queued",
        payload={
            "status": "queued",
        },
    )

    await db.commit()
    await db.refresh(run)

    # Redis 通知失败不影响已经持久化的任务。
    # Worker 后续通过数据库扫描恢复任务。
    try:
        await notify_agent_worker(
            run.id
        )

        await notify_run_event(
            run.id
        )

    except Exception:
        pass

    return run


@router.get(
    "/{run_id}",
    response_model=RunResponse,
)
async def get_run(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户的运行任务。"""

    run = await db.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.tenant_id
            == current_user.tenant_id,
            AgentRun.user_id
            == current_user.id,
        )
    )

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="任务不存在",
        )

    return run


@router.post(
    "/{run_id}/cancel",
    response_model=RunResponse,
)
async def cancel_run(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_CANCEL
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    请求取消任务。

    排队任务可直接取消。
    正在执行的任务写入 Redis 取消标记，
    由 Worker 在安全检查点结束执行。
    """

    run = await db.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.tenant_id
            == current_user.tenant_id,
            AgentRun.user_id
            == current_user.id,
        )
        .with_for_update()
    )

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="任务不存在",
        )

    if run.status in TERMINAL_STATUSES:
        return run

    if run.status in {
        "pending",
        "queued",
        "waiting_approval",
    }:
        run.status = "cancelled"

        from infrastructure.models import (
            utc_now,
        )

        run.finished_at = utc_now()

        await append_run_event(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
            event_type="run.cancelled",
            payload={
                "status": "cancelled",
            },
        )

        await db.commit()
        await db.refresh(run)

        try:
            await notify_run_event(
                run.id
            )

        except Exception:
            pass

        return run

    # running 状态只记录取消请求，
    # 不提前宣称 Worker 已停止。
    await db.commit()

    await get_redis().set(
        f"agent:cancel:{run.id}",
        "1",
        ex=(
            get_settings()
            .run_timeout_seconds
            + 3600
        ),
    )

    return run
