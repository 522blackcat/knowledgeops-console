"""
工具执行幂等。

通过 tenant_id + idempotency_key
防止同一次 LangGraph 恢复重复提交工具。

对于具有外部副作用的工具，还必须将相同
idempotency_key 传递给外部系统（如果支持）。

仅靠本地数据库不能保证：
    外部操作已成功，但进程在记录结果前崩溃
时的严格 exactly-once 语义。
"""

import hashlib
import json
import uuid

from sqlalchemy import select

from infrastructure.database import (
    session_scope,
)

from infrastructure.models import (
    ToolExecution,
)

from tools.registry import (
    ToolDefinition,
)

from tools.validation import (
    validate_tool_arguments,
)


def canonical_arguments(
    arguments: dict,
) -> str:
    """生成稳定的 JSON 参数表示。"""

    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def arguments_hash(
    arguments: dict,
) -> str:
    """计算工具参数 SHA-256。"""

    return hashlib.sha256(
        canonical_arguments(
            arguments
        ).encode("utf-8")
    ).hexdigest()


def tool_idempotency_key(
    *,
    run_id: uuid.UUID,
    tool_call_id: str,
) -> str:
    """同一次工具调用使用固定幂等键。"""

    return (
        f"{run_id}:{tool_call_id}"
    )


async def execute_tool_once(
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tool_call_id: str,
    tool: ToolDefinition,
    arguments: dict,
) -> dict:
    """
    执行一次工具调用。

    已完成的调用直接返回数据库中的结果。

    本模块不绕过人工审批；
    调用方必须先确认工具是否已获批准。
    """

    validate_tool_arguments(
        arguments,
        tool.parameters,
    )

    key = tool_idempotency_key(
        run_id=run_id,
        tool_call_id=tool_call_id,
    )

    async with session_scope() as db:
        existing = await db.scalar(
            select(ToolExecution)
            .where(
                ToolExecution.tenant_id
                == tenant_id,
                ToolExecution.idempotency_key
                == key,
            )
            .with_for_update()
        )

        if existing is not None:
            if (
                existing.tool_name
                != tool.name
            ):
                raise RuntimeError(
                    "幂等键对应的工具名称不一致"
                )

            if existing.status == "completed":
                return existing.result_json

            raise RuntimeError(
                "该工具调用已有未完成的执行记录，"
                "需要先核对外部执行结果"
            )

        record = ToolExecution(
            tenant_id=tenant_id,
            run_id=run_id,
            tool_name=tool.name,
            idempotency_key=key,
            status="started",
            result_json={},
        )

        db.add(record)

    # 不在数据库事务中等待外部工具，
    # 避免长时间持有连接和行锁。
    try:
        raw_result = await tool.handler(
            arguments
        )

        result = {
            "ok": True,
            "result": raw_result,
        }

    except Exception as exc:
        result = {
            "ok": False,
            "error_type": (
                type(exc).__name__
            ),
            "error": str(exc)[:2000],
        }

    async with session_scope() as db:
        record = await db.scalar(
            select(ToolExecution)
            .where(
                ToolExecution.tenant_id
                == tenant_id,
                ToolExecution.idempotency_key
                == key,
            )
            .with_for_update()
        )

        if record is None:
            raise RuntimeError(
                "工具执行记录丢失"
            )

        record.status = (
            "completed"
            if result["ok"]
            else "failed"
        )

        record.result_json = result

    return result
