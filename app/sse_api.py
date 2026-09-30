"""
Agent 运行事件 SSE。

实现方式：
    PostgreSQL 持久化事件；
    Redis Pub/Sub 提醒有新事件；
    定期从 PostgreSQL 补读。

浏览器断线重连：
    使用 Last-Event-ID 恢复。

身份认证：
    使用 Bearer Token。

浏览器原生 EventSource 不能自定义
Authorization Header，前端需要使用
支持 fetch 流式读取的 SSE 客户端。
"""

import asyncio
import json
import uuid

from collections.abc import AsyncIterator

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
)

from fastapi.responses import (
    StreamingResponse,
)

from sqlalchemy import select

from agent.events import (
    run_event_channel,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.rbac import Permission

from infrastructure.database import (
    SessionFactory,
)

from infrastructure.models import (
    AgentRun,
    RunEvent,
)

from infrastructure.redis_client import (
    get_redis,
)


router = APIRouter(
    prefix="/api/runs",
    tags=["运行事件"],
)


TERMINAL_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
    "timed_out",
})


def encode_sse(
    *,
    event_id: int,
    event_type: str,
    payload: dict,
) -> str:
    """编码符合 SSE 格式的事件。"""

    data = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {data}\n\n"
    )


async def stream_run_events(
    *,
    request: Request,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: int,
) -> AsyncIterator[str]:
    """
    先读取持久化事件，再等待 Redis 通知。

    即使 Redis 通知丢失，
    也会定期重新查询 PostgreSQL。
    """

    cursor = last_event_id

    redis = get_redis()
    pubsub = redis.pubsub()

    await pubsub.subscribe(
        run_event_channel(run_id)
    )

    try:
        while True:
            if await request.is_disconnected():
                break

            async with SessionFactory() as db:
                result = await db.execute(
                    select(RunEvent)
                    .where(
                        RunEvent.tenant_id
                        == tenant_id,
                        RunEvent.run_id == run_id,
                        RunEvent.id > cursor,
                    )
                    .order_by(
                        RunEvent.id.asc()
                    )
                    .limit(100)
                )

                events = result.scalars().all()

                run_status = await db.scalar(
                    select(AgentRun.status).where(
                        AgentRun.id == run_id,
                        AgentRun.tenant_id
                        == tenant_id,
                    )
                )

            for event in events:
                cursor = event.id

                yield encode_sse(
                    event_id=event.id,
                    event_type=event.event_type,
                    payload=event.payload_json,
                )

            # 只有事件已全部读取完毕，
            # 才结束已进入终态的任务。
            if (
                run_status in TERMINAL_STATUSES
                and len(events) < 100
            ):
                break

            if len(events) == 100:
                continue

            try:
                await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=5.0,
                )

            except asyncio.CancelledError:
                raise

            except Exception:
                # Redis 通知失败时继续通过
                # PostgreSQL 轮询恢复事件。
                await asyncio.sleep(2)

            yield ": heartbeat\n\n"

    finally:
        await pubsub.unsubscribe(
            run_event_channel(run_id)
        )

        await pubsub.aclose()


@router.get(
    "/{run_id}/events",
)
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
    ),
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_READ
        )
    ),
):
    """建立当前用户任务的 SSE 连接。"""

    async with SessionFactory() as db:
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

    try:
        cursor = int(
            last_event_id or "0"
        )

        if cursor < 0:
            raise ValueError

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Last-Event-ID 无效",
        ) from None

    return StreamingResponse(
        stream_run_events(
            request=request,
            tenant_id=current_user.tenant_id,
            run_id=run_id,
            last_event_id=cursor,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
