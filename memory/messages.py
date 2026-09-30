"""
会话消息持久化。

通过 SELECT ... FOR UPDATE 锁定 Conversation，
保证同一会话并发写入时 sequence_no 不冲突。

调用方负责提交事务。
"""

import uuid

from sqlalchemy import (
    func,
    select,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.models import (
    Conversation,
    ConversationMessage,
)

from memory.short_term import (
    estimate_tokens,
)


async def append_message(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    metadata: dict | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> ConversationMessage:
    """
    追加一条会话消息。

    同一事务中：
        锁定会话；
        查询最大序号；
        插入下一条消息。

    不能在事务外先计算 sequence_no。
    """

    if role not in {
        "system",
        "user",
        "assistant",
        "tool",
    }:
        raise ValueError(
            f"不支持的消息角色：{role}"
        )

    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id
            == tenant_id,
        )
        .with_for_update()
    )

    if conversation is None:
        raise ValueError(
            "会话不存在或不属于当前租户"
        )

    last_sequence = await db.scalar(
        select(
            func.max(
                ConversationMessage.sequence_no
            )
        ).where(
            ConversationMessage.tenant_id
            == tenant_id,
            ConversationMessage.conversation_id
            == conversation_id,
        )
    )

    next_sequence = (
        int(last_sequence or 0) + 1
    )

    message = ConversationMessage(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        sequence_no=next_sequence,
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        token_count=estimate_tokens(
            content
        ),
        metadata_json=(
            metadata or {}
        ),
    )

    db.add(message)

    await db.flush()

    return message
