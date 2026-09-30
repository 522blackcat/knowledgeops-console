"""
会话增量摘要。

算法：
    1. 获取最新摘要；
    2. 读取该摘要尚未覆盖的新消息；
    3. 达到阈值后生成新摘要；
    4. 在事务中再次确认最新摘要版本；
    5. 保存新版本，不删除旧版本。

如果并发期间摘要版本已经变化，
本次结果直接放弃，由下一轮重新生成。

这样避免用过期上下文覆盖更新的摘要。
"""

import uuid

from sqlalchemy import (
    func,
    select,
)

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.model_client import (
    ModelClient,
)

from infrastructure.models import (
    Conversation,
    ConversationMessage,
    ConversationSummary,
)

from memory.short_term import (
    estimate_tokens,
)


SUMMARY_SYSTEM_PROMPT = """
你是会话记忆摘要器。

请根据已有摘要和新增消息生成更新后的摘要。

要求：
1. 保留用户明确表达的目标、约束和已确认决定。
2. 保留尚未完成的任务及重要上下文。
3. 不把模型猜测写成用户事实。
4. 不编造消息中没有的信息。
5. 不保存密码、API Key、访问令牌等敏感凭据。
6. 使用简洁中文。
7. 只输出摘要正文。
""".strip()


def title_from_summary(
    summary_text: str,
) -> str:
    """从摘要里提取稳定会话标题。"""

    clean = " ".join(
        summary_text.strip().split()
    )

    if not clean:
        return "长期会话"

    separators = (
        "。",
        "；",
        ";",
        ".",
        "\n",
    )

    first = clean

    for separator in separators:
        if separator in first:
            first = first.split(
                separator,
                1,
            )[0]

    return (
        first[:28] + "..."
        if len(first) > 28
        else first
    )


async def get_latest_summary(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ConversationSummary | None:
    """读取最新的会话摘要。"""

    return await db.scalar(
        select(ConversationSummary)
        .where(
            ConversationSummary.tenant_id
            == tenant_id,
            ConversationSummary.conversation_id
            == conversation_id,
        )
        .order_by(
            ConversationSummary.version.desc()
        )
        .limit(1)
    )


async def load_uncovered_messages(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    covered_until_sequence: int,
    limit: int = 500,
) -> list[ConversationMessage]:
    """读取上一版摘要尚未覆盖的消息。"""

    result = await db.execute(
        select(ConversationMessage)
        .where(
            ConversationMessage.tenant_id
            == tenant_id,
            ConversationMessage.conversation_id
            == conversation_id,
            ConversationMessage.sequence_no
            > covered_until_sequence,
        )
        .order_by(
            ConversationMessage.sequence_no.asc()
        )
        .limit(limit)
    )

    return result.scalars().all()


def format_summary_input(
    *,
    previous_summary: str,
    messages: list[ConversationMessage],
) -> str:
    """构建摘要模型的输入。"""

    lines = [
        "【上一版摘要】",
        previous_summary or "无",
        "",
        "【新增消息】",
    ]

    for message in messages:
        lines.append(
            f"[{message.sequence_no}] "
            f"{message.role}: "
            f"{message.content}"
        )

    return "\n".join(lines)


async def maybe_create_summary(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ConversationSummary | None:
    """
    达到 Token 阈值时创建增量摘要。

    模型调用期间不持有数据库行锁，
    避免长时间阻塞会话写入。
    """

    settings = get_settings()

    previous = await get_latest_summary(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )

    previous_version = (
        previous.version
        if previous is not None
        else 0
    )

    covered_until = (
        previous.covered_until_sequence
        if previous is not None
        else 0
    )

    new_messages = (
        await load_uncovered_messages(
            db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            covered_until_sequence=(
                covered_until
            ),
        )
    )

    if not new_messages:
        return None

    total_user_messages = await db.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(
            ConversationMessage.tenant_id
            == tenant_id,
            ConversationMessage.conversation_id
            == conversation_id,
            ConversationMessage.role == "user",
        )
    )

    new_token_count = sum(
        message.token_count
        or estimate_tokens(
            message.content
        )
        for message in new_messages
    )

    if (
        new_token_count
        < settings.memory_summary_trigger_tokens
        and (total_user_messages or 0) < 10
    ):
        return None

    summary_input = format_summary_input(
        previous_summary=(
            previous.summary_text
            if previous is not None
            else ""
        ),
        messages=new_messages,
    )

    async with ModelClient() as model:
        response = await model.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        SUMMARY_SYSTEM_PROMPT
                    ),
                },
                {
                    "role": "user",
                    "content": summary_input,
                },
            ],
            temperature=0.1,
            max_tokens=(
                settings
                .memory_summary_token_budget
            ),
        )

    summary_text = (
        response.choices[0]
        .message.content
        or ""
    ).strip()

    if not summary_text:
        raise RuntimeError(
            "摘要模型返回空内容"
        )

    # 模型调用结束后再开启写事务。
    # 锁定会话行，串行化同一会话的摘要提交。
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

    current = await get_latest_summary(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )

    current_version = (
        current.version
        if current is not None
        else 0
    )

    if current_version != previous_version:
        # 并发期间已有其他 Worker
        # 创建了更新的摘要。
        return None

    new_summary = ConversationSummary(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        version=previous_version + 1,
        previous_summary_id=(
            previous.id
            if previous is not None
            else None
        ),
        covered_until_sequence=(
            new_messages[-1].sequence_no
        ),
        summary_text=summary_text,
        token_count=estimate_tokens(
            summary_text
        ),
    )

    db.add(new_summary)

    if (total_user_messages or 0) >= 10:
        conversation.title = (
            title_from_summary(
                summary_text
            )
        )

    await db.flush()

    return new_summary
