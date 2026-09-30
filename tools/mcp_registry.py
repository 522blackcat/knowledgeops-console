"""
将 MCP 工具注册到 Agent 工具表。

注册名称采用：
    mcp__服务名__工具名

避免不同 MCP 服务的工具名称冲突。
"""

from tools.mcp_client import (
    call_mcp_tool,
    discover_mcp_tools,
)

from tools.mcp_config import (
    load_mcp_servers,
)

from tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
)


def mcp_tool_name(
    server_name: str,
    tool_name: str,
) -> str:
    """生成 Agent 可见的 MCP 工具名称。"""

    return (
        f"mcp__{server_name}"
        f"__{tool_name}"
    )


async def register_mcp_tools(
    registry: ToolRegistry,
) -> None:
    """
    发现并注册所有服务端允许的 MCP 工具。

    未在 allowed_tools 中列出的工具
    不会暴露给模型。
    """

    for config in load_mcp_servers():
        discovered = (
            await discover_mcp_tools(
                config
            )
        )

        for remote_tool in discovered:
            if remote_tool.name not in (
                config.allowed_tools
            ):
                continue

            # 未明确声明风险等级的 MCP 工具，
            # 默认视为高风险，不按只读处理。
            risk = (
                config.tool_risks.get(
                    remote_tool.name,
                    ToolRisk.DANGEROUS,
                )
            )

            async def handler(
                arguments: dict,
                *,
                _config=config,
                _name=remote_tool.name,
            ):
                return await call_mcp_tool(
                    config=_config,
                    tool_name=_name,
                    arguments=arguments,
                )

            registry.register(
                ToolDefinition(
                    name=mcp_tool_name(
                        config.name,
                        remote_tool.name,
                    ),
                    description=(
                        remote_tool.description
                        or remote_tool.name
                    ),
                    parameters=(
                        remote_tool.inputSchema
                        or {
                            "type": "object",
                            "properties": {},
                        }
                    ),
                    risk=risk,
                    handler=handler,
                    source=(
                        f"mcp:{config.name}"
                    ),
                )
            )
