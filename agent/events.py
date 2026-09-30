"""
Agent 运行事件。

PostgreSQL：
    保存可恢复、可审计的事件。

Redis Pub/Sub：
    只负责通知 SSE 连接有新事件。

SSE 即使错过 Redis 通知，
也可以根据 RunEvent.id 从数据库补读。
"""

import uuid

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.models import (
    RunEvent,
)

from infrastructure.redis_client import (
    get_redis,
)


def run_event_channel(
    run_id: uuid.UUID,
) -> str:
    """生成运行事件通知频道。"""

    return f"agent:events:{run_id}"


async def append_run_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict,
) -> RunEvent:
    """
    在调用方事务中新增事件。

    本函数只 flush，不提交。
    调用方必须先提交事务，再通知 Redis。
    """

    event = RunEvent(
        tenant_id=tenant_id,
        run_id=run_id,
        event_type=event_type,
        payload_json=payload,
    )

    db.add(event)

    await db.flush()

    return event


async def notify_run_event(
    run_id: uuid.UUID,
) -> None:
    """
    发送非持久化通知。

    Redis 不可用时允许上层记录日志，
    不应回滚已成功提交的业务事务。
    """

    redis = get_redis()

    await redis.publish(
        run_event_channel(run_id),
        "new_event",
    )


async def notify_agent_worker(
    run_id: uuid.UUID,
) -> None:
    """
    通知 Agent Worker 有新任务。

    真实任务仍在 PostgreSQL 中。
    Worker 启动和定期扫描时可恢复漏掉的通知。
    """

    redis = get_redis()

    await redis.publish(
        "agent:worker:wakeup",
        str(run_id),
    )
