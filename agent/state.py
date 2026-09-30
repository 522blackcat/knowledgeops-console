"""
LangGraph Agent 状态。

State 只保存图恢复所需的执行状态。
完整会话历史由 PostgreSQL ConversationMessage 保存。
"""

from typing import (
    Annotated,
    Any,
    TypedDict,
)

from langgraph.graph.message import (
    add_messages,
)


class AgentState(TypedDict, total=False):
    """
    Agent 图状态。

    messages：
        LangGraph 消息列表。

    run_id / tenant_id / user_id：
        由服务端创建，不接受模型修改。

    pending_tool_calls：
        当前待执行的工具调用。

    tool_results：
        已完成的工具结果。

    iteration：
        当前模型循环次数。

    final_answer：
        最终回答。
    """

    messages: Annotated[
        list,
        add_messages,
    ]

    run_id: str
    tenant_id: str
    user_id: str
    agent_id: str
    conversation_id: str

    question: str

    system_prompt: str

    allowed_tools: list[str]

    knowledge_base_ids: list[str]

    pending_tool_calls: list[dict]

    tool_results: list[dict]

    iteration: int
    tool_call_count: int

    final_answer: str

    error_code: str
    error_message: str

    cancelled: bool

    metadata: dict[str, Any]
