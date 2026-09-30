"""
LangGraph Agent 执行图。

图结构：

    START
      |
    model
      |
  有工具调用？
    /      \
   否       是
   |        |
  END     tools
            |
      是否需要审批？
            |
      interrupt / resume
            |
         model

图只处理一次 AgentRun。
任务领取、租约、超时和取消由 Worker 负责。
"""

import json
import uuid

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from langgraph.types import (
    interrupt,
)

from agent.approval import (
    create_approval_request,
    get_approval_decision,
)

from agent.state import (
    AgentState,
)

from infrastructure.config import (
    get_settings,
)

from infrastructure.model_client import (
    ModelClient,
)

from tools.execution import (
    execute_tool_once,
)

from tools.registry import (
    ToolRegistry,
    validate_tool_access,
)

from tools.validation import (
    parse_tool_arguments,
)


def serialize_tool_result(
    result: dict,
) -> str:
    """将工具结果转换为模型消息。"""

    return json.dumps(
        result,
        ensure_ascii=False,
        default=str,
    )


def build_agent_graph(
    *,
    registry: ToolRegistry,
    checkpointer,
):
    """
    构建带 PostgreSQL Checkpoint 的 Agent 图。

    registry 是当前 Agent 的工具注册表，
    不是所有租户共享的无限制工具集合。
    """

    settings = get_settings()

    async def model_node(
        state: AgentState,
    ) -> dict:
        """调用模型并决定是否使用工具。"""

        iteration = (
            state.get(
                "iteration",
                0,
            )
            + 1
        )

        if iteration > (
            settings.model_max_rounds
        ):
            return {
                "final_answer": (
                    "已达到本次任务的最大推理轮数，"
                    "请缩小问题范围后重试。"
                ),
                "pending_tool_calls": [],
                "iteration": iteration,
            }

        allowed_tools = (
            state.get(
                "allowed_tools",
                [],
            )
        )

        tool_specs = (
            registry.openai_tools(
                allowed_tools
            )
        )

        # ModelClient 使用 OpenAI 兼容接口。
        # 这里显式转换 LangGraph 消息，
        # 不直接将 LangChain 消息对象
        # 传给 OpenAI SDK。
        api_messages = []

        for message in state.get(
            "messages",
            [],
        ):
            if isinstance(
                message,
                SystemMessage,
            ):
                api_messages.append({
                    "role": "system",
                    "content": message.content,
                })

            elif isinstance(
                message,
                HumanMessage,
            ):
                api_messages.append({
                    "role": "user",
                    "content": message.content,
                })

            elif isinstance(
                message,
                AIMessage,
            ):
                item = {
                    "role": "assistant",
                    "content": (
                        message.content
                        or ""
                    ),
                }

                if message.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": (
                                    json.dumps(
                                        call["args"],
                                        ensure_ascii=False,
                                    )
                                ),
                            },
                        }
                        for call in (
                            message.tool_calls
                        )
                    ]

                api_messages.append(
                    item
                )

            elif isinstance(
                message,
                ToolMessage,
            ):
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": (
                        message.tool_call_id
                    ),
                    "content": (
                        message.content
                    ),
                })

        async with ModelClient() as model:
            response = await model.chat(
                messages=api_messages,
                tools=(
                    tool_specs
                    if tool_specs
                    else None
                ),
                temperature=0.2,
            )

        answer = (
            response.choices[0]
            .message
        )

        raw_calls = (
            answer.tool_calls
            or []
        )

        if not raw_calls:
            final_answer = (
                answer.content
                or ""
            ).strip()

            return {
                "messages": [
                    AIMessage(
                        content=final_answer
                    )
                ],
                "final_answer": (
                    final_answer
                ),
                "pending_tool_calls": [],
                "iteration": iteration,
            }

        tool_calls = []

        for call in raw_calls:
            arguments = (
                parse_tool_arguments(
                    call.function.arguments
                )
            )

            tool_calls.append({
                "id": call.id,
                "name": (
                    call.function.name
                ),
                "args": arguments,
            })

        current_count = state.get(
            "tool_call_count",
            0,
        )

        if (
            current_count
            + len(tool_calls)
            > settings.model_max_tool_calls
        ):
            return {
                "final_answer": (
                    "已达到本次任务的最大工具"
                    "调用次数，任务已停止。"
                ),
                "pending_tool_calls": [],
                "iteration": iteration,
            }

        return {
            "messages": [
                AIMessage(
                    content=(
                        answer.content
                        or ""
                    ),
                    tool_calls=tool_calls,
                )
            ],
            "pending_tool_calls": (
                tool_calls
            ),
            "tool_call_count": (
                current_count
                + len(tool_calls)
            ),
            "iteration": iteration,
        }

    async def tools_node(
        state: AgentState,
    ) -> dict:
        """
        顺序执行模型请求的工具。

        高风险工具先持久化审批请求，
        再调用 interrupt()。
        """

        tenant_id = uuid.UUID(
            state["tenant_id"]
        )

        run_id = uuid.UUID(
            state["run_id"]
        )

        user_id = uuid.UUID(
            state["user_id"]
        )

        allowed_names = (
            state.get(
                "allowed_tools",
                [],
            )
        )

        result_messages = []
        tool_results = []

        for call in state.get(
            "pending_tool_calls",
            [],
        ):
            tool_name = call["name"]
            tool_call_id = call["id"]
            arguments = call["args"]

            tool = validate_tool_access(
                registry=registry,
                allowed_names=allowed_names,
                requested_name=tool_name,
            )

            if tool.requires_approval:
                await create_approval_request(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    user_id=user_id,
                    tool_call_id=(
                        tool_call_id
                    ),
                    tool_name=tool_name,
                    arguments=arguments,
                )

                # 节点恢复时会从头重新执行。
            # 审批结果必须以 PostgreSQL 为准。
            decision = (
                await get_approval_decision(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    tool_call_id=(
                        tool_call_id
                    ),
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )

            if decision == "pending":
                interrupt({
                    "type": "tool_approval",
                    "run_id": str(
                        run_id
                    ),
                    "tool_call_id": (
                        tool_call_id
                    ),
                    "tool_name": (
                        tool_name
                    ),
                    "arguments": (
                        arguments
                    ),
                })

                # 只有图被合法恢复后，
                # 才会继续执行到这里。
                decision = (
                    await get_approval_decision(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        tool_call_id=(
                            tool_call_id
                        ),
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                )

                if decision == "rejected":
                    result_messages.append(
                        ToolMessage(
                            tool_call_id=(
                                tool_call_id
                            ),
                            content=(
                                "人工审批已拒绝此工具调用，"
                                "不得执行该操作。"
                            ),
                        )
                    )

                    continue

                if decision != "approved":
                    raise RuntimeError(
                        "工具调用尚未获得有效审批"
                    )

            result = await execute_tool_once(
                tenant_id=tenant_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool=tool,
                arguments=arguments,
            )
            tool_results.append({
                "tool_name": tool_name,
                "result": result,
            })

            result_messages.append(
                ToolMessage(
                    tool_call_id=(
                        tool_call_id
                    ),
                    content=(
                        serialize_tool_result(
                            result
                        )
                    ),
                )
            )

        return {
            "messages": result_messages,
            "pending_tool_calls": [],
            "tool_results": tool_results,
        }

    def route_after_model(
        state: AgentState,
    ) -> str:
        """决定结束还是执行工具。"""

        if state.get(
            "final_answer"
        ):
            return "end"

        if state.get(
            "pending_tool_calls"
        ):
            return "tools"

        return "end"

    graph = StateGraph(
        AgentState
    )

    graph.add_node(
        "model",
        model_node,
    )

    graph.add_node(
        "tools",
        tools_node,
    )

    graph.add_edge(
        START,
        "model",
    )

    graph.add_conditional_edges(
        "model",
        route_after_model,
        {
            "tools": "tools",
            "end": END,
        },
    )

    graph.add_edge(
        "tools",
        "model",
    )

    return graph.compile(
        checkpointer=checkpointer
    )
