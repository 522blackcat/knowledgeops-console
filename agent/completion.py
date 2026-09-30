"""
Agent 完成事务。

AgentRun 状态、用户消息、助手消息和 RunEvent
在同一个 PostgreSQL 事务内提交。

使用消息 metadata_json 中的 run_id 和 role
识别已写入的消息，避免 Worker 恢复时重复追加。

注意：
这里通过锁定 Conversation 串行化消息写入，
同一会话的其他写入路径也必须使用
memory.messages.append_message()。
"""

import uuid

from sqlalchemy import select

from agent.events import (
    append_run_event,
    notify_run_event,
)

from infrastructure.database import (
    session_scope,
)

from infrastructure.models import (
    AgentRun,
    Conversation,
    ConversationMessage,
    utc_now,
)

from memory.messages import (
    append_message,
)


PLACEHOLDER_TITLES = (
    "新会话",
    "未命名会话",
)


def should_replace_title(
    title: str,
) -> bool:
    """判断会话标题是否仍是临时标题。"""

    clean = (title or "").strip()

    return (
        not clean
        or clean in PLACEHOLDER_TITLES
        or clean.startswith("前端会话 ")
    )


def title_from_question(
    question: str,
) -> str:
    """用首次提问生成可读标题。"""

    clean = " ".join(
        question.strip().split()
    )

    if not clean:
        return "新会话"

    return (
        clean[:32] + "..."
        if len(clean) > 32
        else clean
    )


async def complete_run_with_messages(
    *,
    run_id: uuid.UUID,
    worker_id: str,
    answer: str,
    metadata: dict | None = None,
) -> bool:
    """
    原子提交任务完成状态和两条会话消息。

    返回 False 表示任务已被其他流程处理。
    """

    async with session_scope() as db:
        run = await db.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.status == "running",
                AgentRun.lease_owner
                == worker_id,
            )
            .with_for_update()
        )

        if run is None:
            return False

        # append_message() 会锁定 Conversation，
        # 因此同一会话的序号分配保持串行。
        existing_result = await db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.tenant_id
                == run.tenant_id,
                ConversationMessage.conversation_id
                == run.conversation_id,
                ConversationMessage.metadata_json[
                    "run_id"
                ].as_string()
                == str(run.id),
            )
        )

        existing_roles = {
            message.role
            for message in (
                existing_result.scalars().all()
            )
        }

        if "user" not in existing_roles:
            await append_message(
                db,
                tenant_id=run.tenant_id,
                conversation_id=(
                    run.conversation_id
                ),
                role="user",
                content=run.question,
                metadata={
                    "run_id": str(
                        run.id
                    ),
                },
            )

            conversation = await db.scalar(
                select(Conversation)
                .where(
                    Conversation.id
                    == run.conversation_id,
                    Conversation.tenant_id
                    == run.tenant_id,
                )
                .with_for_update()
            )

            if (
                conversation is not None
                and should_replace_title(
                    conversation.title
                )
            ):
                conversation.title = (
                    title_from_question(
                        run.question
                    )
                )

        if "assistant" not in existing_roles:
            await append_message(
                db,
                tenant_id=run.tenant_id,
                conversation_id=(
                    run.conversation_id
                ),
                role="assistant",
                content=answer,
                metadata={
                    "run_id": str(
                        run.id
                    ),
                    **(metadata or {}),
                },
            )

        run.answer = answer
        run.status = "completed"
        run.finished_at = utc_now()
        run.lease_owner = None
        run.lease_until = None
        run.error_code = None
        run.error_message = None

        await append_run_event(
            db,
            tenant_id=run.tenant_id,
            run_id=run.id,
            event_type="run.completed",
            payload={
                "status": "completed",
                "answer": answer,
            },
        )

    try:
        await notify_run_event(
            run_id
        )

    except Exception:
        # 事件已持久化；
        # SSE 可通过数据库补读。
        pass

    return True
