"""FastAPI 请求与响应模型。"""

import uuid

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class APIModel(BaseModel):
    """统一响应模型配置。"""

    model_config = ConfigDict(
        from_attributes=True,
    )


class LoginRequest(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=100,
    )

    password: str = Field(
        min_length=1,
    )


class LoginResponse(APIModel):
    access_token: str
    token_type: str = "bearer"


class AgentCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=150,
    )

    description: str = ""

    agent_type: str = "general"

    configuration: dict = Field(
        default_factory=dict
    )


class AgentUpdateRequest(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=150,
    )

    description: str | None = None

    agent_type: str | None = None

    configuration: dict | None = None

    status: str | None = None


class AgentResponse(APIModel):
    id: uuid.UUID
    name: str
    description: str
    agent_type: str
    status: str
    configuration: dict


class ConversationCreateRequest(BaseModel):
    agent_id: uuid.UUID
    title: str = Field(
        default="新会话",
        max_length=255,
    )


class ConversationResponse(APIModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    title: str
    status: str
    created_at: datetime


class RunCreateRequest(BaseModel):
    agent_id: uuid.UUID
    conversation_id: uuid.UUID

    question: str = Field(
        min_length=1,
    )

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=150,
    )


class RunResponse(APIModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    question: str
    answer: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ApprovalResponse(APIModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tool_name: str
    arguments_json: dict
    status: str
    review_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None
    approval_wait_ms: int | None = None


class ApprovalDecisionRequest(BaseModel):
    reason: str = Field(
        default="",
        max_length=2000,
    )


class MessageResponse(APIModel):
    id: uuid.UUID
    sequence_no: int
    role: str
    content: str
    metadata_json: dict = Field(
        default_factory=dict
    )
    created_at: datetime
