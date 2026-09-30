"""
短期记忆。

负责：
    1. 估算消息 Token；
    2. 读取最近消息；
    3. 按 Token 预算构建上下文。

Token 估算仅用于预算控制，
不代表具体模型的精确 tokenizer 结果。
"""

import uuid

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.models import (
    ConversationMessage,
)


def estimate_tokens(
    text: str,
) -> int:
    """
    粗略估算中英文混合文本 Token 数。

    中文按字符估算；
    英文按约四字符一个 Token 估算。

    生产环境如需精确限制，应接入
    当前模型对应的 tokenizer。
    """

    if not text:
        return 0

    chinese_count = sum(
        1
        for char in text
        if "\u4e00" <= char <= "\u9fff"
    )

    other_count = (
        len(text) - chinese_count
    )

    return max(
        1,
        chinese_count
        + (other_count + 3) // 4,
    )


async def load_recent_messages(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int = 100,
) -> list[ConversationMessage]:
    """按时间顺序读取最近的会话消息。"""

    result = await db.execute(
        select(ConversationMessage)
        .where(
            ConversationMessage.tenant_id
            == tenant_id,
            ConversationMessage.conversation_id
            == conversation_id,
        )
        .order_by(
            ConversationMessage.sequence_no.desc()
        )
        .limit(limit)
    )

    return list(
        reversed(
            result.scalars().all()
        )
    )


def fit_messages_to_budget(
    messages: list[ConversationMessage],
    *,
    token_budget: int,
) -> list[ConversationMessage]:
    """
    从最新消息向前选择，直到达到预算。

    保持最终结果的原始时间顺序。
    """

    if token_budget <= 0:
        raise ValueError(
            "token_budget 必须大于零"
        )

    selected = []
    used_tokens = 0

    for message in reversed(messages):
        count = (
            message.token_count
            or estimate_tokens(
                message.content
            )
        )

        if (
            used_tokens + count
            > token_budget
        ):
            break

        selected.append(message)

        used_tokens += count

    return list(
        reversed(selected)
    )


def to_model_messages(
    messages: list[ConversationMessage],
) -> list[dict]:
    """
    转换为模型消息格式。

    此处只转换普通文本消息。
    工具调用消息由 Agent 执行器维护，
    避免丢失 tool_call_id 与工具调用结构。
    """

    result = []

    for message in messages:
        if message.role not in {
            "system",
            "user",
            "assistant",
        }:
            continue

        result.append({
            "role": message.role,
            "content": message.content,
        })

    return result
