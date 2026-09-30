"""
Agent 工具注册表。

工具由服务端注册。
模型不能自行指定任意 Python 函数、
任意 MCP 服务地址或任意 HTTP 目标。

风险等级：
    read：
        只读工具。

    write：
        会修改业务数据。

    dangerous：
        可能造成不可逆外部影响。

write / dangerous 默认要求人工审批。
"""

from dataclasses import dataclass
from enum import StrEnum

from typing import (
    Any,
    Awaitable,
    Callable,
)


class ToolRisk(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


ToolHandler = Callable[
    [dict[str, Any]],
    Awaitable[Any],
]


@dataclass(frozen=True)
class ToolDefinition:
    """一个服务端允许使用的工具。"""

    name: str

    description: str

    parameters: dict

    risk: ToolRisk

    handler: ToolHandler

    source: str = "builtin"

    @property
    def requires_approval(self) -> bool:
        """判断工具是否必须经过人工审批。"""

        return self.risk in {
            ToolRisk.WRITE,
            ToolRisk.DANGEROUS,
        }

    def to_openai_tool(self) -> dict:
        """转换为 OpenAI Function Calling 格式。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """按工具名称管理服务端工具。"""

    def __init__(self):
        self._tools: dict[
            str,
            ToolDefinition,
        ] = {}

    def register(
        self,
        tool: ToolDefinition,
    ) -> None:
        """注册工具，拒绝重名。"""

        if tool.name in self._tools:
            raise ValueError(
                f"工具名称重复：{tool.name}"
            )

        self._tools[tool.name] = tool

    def get(
        self,
        name: str,
    ) -> ToolDefinition:
        """获取已注册工具。"""

        tool = self._tools.get(
            name
        )

        if tool is None:
            raise KeyError(
                f"工具未注册：{name}"
            )

        return tool

    def get_allowed(
        self,
        allowed_names: list[str],
    ) -> list[ToolDefinition]:
        """
        仅返回 Agent 配置允许的工具。

        不存在的名称直接报错，
        避免配置错误被静默忽略。
        """

        return [
            self.get(name)
            for name in allowed_names
        ]

    def openai_tools(
        self,
        allowed_names: list[str],
    ) -> list[dict]:
        """生成当前 Agent 可见的工具描述。"""

        return [
            tool.to_openai_tool()
            for tool in self.get_allowed(
                allowed_names
            )
        ]


def validate_tool_access(
    *,
    registry: ToolRegistry,
    allowed_names: list[str],
    requested_name: str,
) -> ToolDefinition:
    """
    工具执行前再次检查白名单。

    不能只依赖模型调用时提供的 tools 参数，
    因为模型返回的名称仍属于不可信输入。
    """

    if requested_name not in allowed_names:
        raise PermissionError(
            f"Agent 未获授权使用工具："
            f"{requested_name}"
        )

    return registry.get(
        requested_name
    )
