"""Agent 定义和会话管理接口。"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.dependencies import (
    CurrentUser,
    require_permission,
)

from app.audit import write_audit_log

from app.rbac import Permission

from app.schemas import (
    AgentCreateRequest,
    AgentResponse,
    AgentUpdateRequest,
    ConversationCreateRequest,
    ConversationResponse,
    MessageResponse,
)

from infrastructure.database import (
    get_db,
)

from infrastructure.models import (
    AgentDefinition,
    Conversation,
    ConversationMessage,
)


router = APIRouter(
    prefix="/api",
    tags=["Agent 与会话"],
)


def ensure_prompt_version(
    configuration: dict | None,
) -> dict:
    """Return a config with a stable prompt_version."""

    config = dict(configuration or {})
    config.setdefault("prompt_version", "v1")
    return config


def bump_prompt_version(
    value: str | None,
) -> str:
    """Increment simple vN prompt versions."""

    clean = str(value or "v1").strip()

    if clean.startswith("v") and clean[1:].isdigit():
        return f"v{int(clean[1:]) + 1}"

    return f"{clean}+1"


AUDITED_CONFIGURATION_KEYS = (
    "system_prompt",
    "prompt_version",
    "knowledge_scope",
    "knowledge_base_ids",
    "retrieval_mode",
    "allowed_tools",
)


def changed_fields(
    before: dict,
    after: dict,
    keys: tuple[str, ...],
) -> dict:
    """Build a compact before/after diff for selected keys."""

    diff = {}

    for key in keys:
        old = before.get(key)
        new = after.get(key)

        if old != new:
            diff[key] = {
                "before": old,
                "after": new,
            }

    return diff


def agent_audit_changes(
    *,
    before: dict,
    after: AgentDefinition,
) -> dict:
    """Return structured changes for an Agent update audit log."""

    top_level = changed_fields(
        before,
        {
            "name": after.name,
            "description": after.description,
            "agent_type": after.agent_type,
            "status": after.status,
        },
        (
            "name",
            "description",
            "agent_type",
            "status",
        ),
    )

    configuration_diff = changed_fields(
        before.get("configuration", {}),
        after.configuration or {},
        AUDITED_CONFIGURATION_KEYS,
    )

    return {
        "fields": top_level,
        "configuration": configuration_diff,
        "changed_keys": sorted([
            *top_level.keys(),
            *(
                f"configuration.{key}"
                for key in configuration_diff.keys()
            ),
        ]),
    }


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
)
async def create_agent(
    body: AgentCreateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.AGENT_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """在当前租户下创建 Agent。"""

    agent = AgentDefinition(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        agent_type=body.agent_type,
        configuration=ensure_prompt_version(
            body.configuration
        ),
        status="idle",
    )

    db.add(agent)

    await db.flush()
    await write_audit_log(
        db,
        current_user=current_user,
        action="agent.create",
        resource_type="agent",
        resource_id=str(agent.id),
        summary=f"创建 Agent：{agent.name}",
        metadata={
            "name": agent.name,
            "agent_type": agent.agent_type,
            "configuration": agent.configuration,
        },
    )

    await db.commit()
    await db.refresh(agent)

    return agent


@router.get(
    "/agents",
    response_model=list[AgentResponse],
)
async def list_agents(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.AGENT_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """只列出当前租户的 Agent。"""

    result = await db.execute(
        select(AgentDefinition)
        .where(
            AgentDefinition.tenant_id
            == current_user.tenant_id
        )
        .order_by(
            AgentDefinition.created_at.desc()
        )
        .limit(100)
    )

    return result.scalars().all()


@router.patch(
    "/agents/{agent_id}",
    response_model=AgentResponse,
)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.AGENT_WRITE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """修改当前租户下的 Agent 配置。"""

    agent = await db.scalar(
        select(AgentDefinition)
        .where(
            AgentDefinition.id == agent_id,
            AgentDefinition.tenant_id
            == current_user.tenant_id,
        )
        .with_for_update()
    )

    if agent is None:
        raise HTTPException(
            status_code=404,
            detail="Agent 不存在",
        )

    before = {
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "status": agent.status,
        "configuration": dict(
            agent.configuration or {}
        ),
    }

    if body.name is not None:
        agent.name = body.name

    if body.description is not None:
        agent.description = body.description

    if body.agent_type is not None:
        agent.agent_type = body.agent_type

    if body.configuration is not None:
        previous_configuration = dict(
            agent.configuration or {}
        )
        next_configuration = ensure_prompt_version(
            body.configuration
        )

        if (
            next_configuration.get("system_prompt")
            != previous_configuration.get(
                "system_prompt"
            )
            and next_configuration.get(
                "prompt_version"
            )
            == previous_configuration.get(
                "prompt_version",
                "v1",
            )
        ):
            next_configuration["prompt_version"] = (
                bump_prompt_version(
                    previous_configuration.get(
                        "prompt_version"
                    )
                )
            )

        agent.configuration = next_configuration

    if body.status is not None:
        if body.status not in {
            "idle",
            "disabled",
        }:
            raise HTTPException(
                status_code=422,
                detail="status 只能是 idle 或 disabled",
            )
        agent.status = body.status

    changes = agent_audit_changes(
        before=before,
        after=agent,
    )

    await write_audit_log(
        db,
        current_user=current_user,
        action="agent.update",
        resource_type="agent",
        resource_id=str(agent.id),
        summary=f"修改 Agent：{agent.name}",
        metadata={
            "name": agent.name,
            "description": agent.description,
            "agent_type": agent.agent_type,
            "status": agent.status,
            "configuration": agent.configuration,
            "changes": changes,
            "configuration_diff": (
                changes["configuration"]
            ),
        },
    )

    await db.commit()
    await db.refresh(agent)

    return agent


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    body: ConversationCreateRequest,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_CREATE
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """创建属于当前用户的会话。"""

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

    conversation = Conversation(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        agent_id=agent.id,
        title=body.title,
        status="active",
    )

    db.add(conversation)

    await db.commit()
    await db.refresh(conversation)

    return conversation


@router.get(
    "/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的历史会话，最近创建的排在前面。"""

    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id
            == current_user.tenant_id,
            Conversation.user_id
            == current_user.id,
        )
        .order_by(
            Conversation.created_at.desc()
        )
        .limit(100)
    )

    return result.scalars().all()


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    conversation_id: uuid.UUID,
    current_user: CurrentUser = Depends(
        require_permission(
            Permission.RUN_READ
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户会话的最近消息。"""

    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id
            == current_user.tenant_id,
            Conversation.user_id
            == current_user.id,
        )
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在",
        )

    result = await db.execute(
        select(ConversationMessage)
        .where(
            ConversationMessage.tenant_id
            == current_user.tenant_id,
            ConversationMessage.conversation_id
            == conversation_id,
        )
        .order_by(
            ConversationMessage.sequence_no.desc()
        )
        .limit(100)
    )

    messages = result.scalars().all()

    return list(reversed(messages))
