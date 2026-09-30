"""
真实 MCP 工具客户端。

支持：
    stdio
    streamable_http

MCP 会话只在工具调用期间保持连接。
后续可根据性能需求实现长连接池。

注意：
stdio command 会启动本地进程。
只能由受信任的部署管理员配置，
绝不能允许普通用户或模型修改。
"""

import asyncio
import os

from contextlib import (
    AsyncExitStack,
    asynccontextmanager,
)

from mcp import (
    ClientSession,
    StdioServerParameters,
)

from mcp.client.stdio import (
    stdio_client,
)

from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

from infrastructure.config import (
    get_settings,
)

from tools.mcp_config import (
    MCPServerConfig,
)


@asynccontextmanager
async def open_mcp_session(
    config: MCPServerConfig,
):
    """
    建立 MCP 连接并初始化会话。

    使用 AsyncExitStack 统一释放资源。
    """

    settings = get_settings()

    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            parameters = (
                StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env={
                        **os.environ,
                        **config.env,
                    },
                )
            )

            read_stream, write_stream = (
                await stack.enter_async_context(
                    stdio_client(
                        parameters
                    )
                )
            )

        elif (
            config.transport
            == "streamable_http"
        ):
            (
                read_stream,
                write_stream,
                _,
            ) = (
                await stack.enter_async_context(
                    streamablehttp_client(
                        config.url
                    )
                )
            )

        else:
            raise ValueError(
                "不支持的 MCP transport"
            )

        session = (
            await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                )
            )
        )

        await asyncio.wait_for(
            session.initialize(),
            timeout=(
                settings
                .mcp_connect_timeout_seconds
            ),
        )

        yield session


async def discover_mcp_tools(
    config: MCPServerConfig,
):
    """
    获取 MCP 服务公开的工具列表。

    发现结果不等于授权结果。
    调用方仍必须按 allowed_tools 过滤。
    """

    async with open_mcp_session(
        config
    ) as session:
        response = await session.list_tools()

        return response.tools


async def call_mcp_tool(
    *,
    config: MCPServerConfig,
    tool_name: str,
    arguments: dict,
) -> dict:
    """
    执行一次真实 MCP 工具调用。

    工具名称必须同时满足：
        1. MCP 服务实际公开；
        2. 服务端 allowed_tools 白名单。
    """

    if tool_name not in (
        config.allowed_tools
    ):
        raise PermissionError(
            f"MCP 工具未授权："
            f"{config.name}/{tool_name}"
        )

    settings = get_settings()

    async with open_mcp_session(
        config
    ) as session:
        available = (
            await session.list_tools()
        )

        available_names = {
            tool.name
            for tool in available.tools
        }

        if tool_name not in available_names:
            raise ValueError(
                f"MCP 服务未提供工具："
                f"{tool_name}"
            )

        result = await asyncio.wait_for(
            session.call_tool(
                tool_name,
                arguments=arguments,
            ),
            timeout=(
                settings
                .mcp_tool_timeout_seconds
            ),
        )

        # MCP 返回结构可能包含文本、
        # 图片或其他内容块。
        # 统一转换为可序列化的字典。
        return result.model_dump(
            mode="json"
        )
