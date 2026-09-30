"""
LangGraph 人工审批持久化。

审批请求以 run_id + tool_call_id 唯一定位。

图节点调用 interrupt() 前，
必须先提交审批请求到 PostgreSQL，
这样前端才能看到待审批记录。
"""

import uuid

from sqlalchemy import select

from infrastructure.database import (
    session_scope,
)

from infrastructure.models import (
    AgentRun,
    ApprovalRequest,
)

from tools.execution import (
    arguments_hash,
)


async def create_approval_request(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    arguments: dict,
) -> ApprovalRequest:
    """创建或读取当前工具调用的审批请求。"""

    digest = arguments_hash(
        arguments
    )

    async with session_scope() as db:
        run = await db.scalar(
            select(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.tenant_id
                == tenant_id,
            )
            .with_for_update()
        )

        if run is None:
            raise RuntimeError(
                "审批关联的运行任务不存在"
            )

        approval = await db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id
                == tenant_id,
                ApprovalRequest.run_id
                == run_id,
                ApprovalRequest.tool_call_id
                == tool_call_id,
            )
        )

        if approval is not None:
            if (
                approval.tool_name
                != tool_name
                or approval.arguments_hash
                != digest
            ):
                raise RuntimeError(
                    "同一工具调用的审批参数发生变化"
                )

            return approval

        approval = ApprovalRequest(
            tenant_id=tenant_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_json=arguments,
            arguments_hash=digest,
            status="pending",
            requested_by=user_id,
        )

        db.add(approval)

        # 状态切换和租约释放统一由 Worker
        # 在 LangGraph Checkpoint 完成后处理。
        await db.flush()

        return approval


async def get_approval_decision(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    arguments: dict,
) -> str:
    """
    重新读取数据库中的审批决定。

    不信任 Command(resume=...) 自带的决定，
    因为真正的批准者和审批记录在 PostgreSQL。
    """

    async with session_scope() as db:
        approval = await db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id
                == tenant_id,
                ApprovalRequest.run_id
                == run_id,
                ApprovalRequest.tool_call_id
                == tool_call_id,
            )
        )

        if approval is None:
            raise RuntimeError(
                "审批请求不存在"
            )

        if (
            approval.tool_name
            != tool_name
            or approval.arguments_hash
            != arguments_hash(
                arguments
            )
        ):
            raise RuntimeError(
                "工具调用与审批记录不一致"
            )

        return approval.status
