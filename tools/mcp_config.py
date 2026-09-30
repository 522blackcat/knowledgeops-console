"""
MCP 服务配置加载。

MCP 服务地址、命令和环境变量只能来自
服务端配置文件，不能来自用户问题或模型输出。

配置文件格式见：
    mcp_servers.example.json
"""

import json
import re

from dataclasses import dataclass
from pathlib import Path

from infrastructure.config import (
    get_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tools.registry import (
    ToolRisk,
)


SAFE_NAME = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"
)


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str

    command: str | None
    args: tuple[str, ...]

    url: str | None

    env: dict[str, str]

    allowed_tools: frozenset[str]

    tool_risks: dict[str, ToolRisk]


def resolve_mcp_config_path() -> Path:
    """解析服务端 MCP 配置路径。"""

    configured = Path(
        get_settings().mcp_config_path
    )

    if configured.is_absolute():
        return configured

    return (
        PROJECT_ROOT / configured
    ).resolve()


def load_mcp_servers() -> list[
    MCPServerConfig
]:
    """读取并验证 MCP 服务配置。"""

    path = resolve_mcp_config_path()

    if not path.is_file():
        # 没有配置 MCP 服务时，
        # Agent 仍可使用内置工具。
        return []

    document = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    servers = document.get(
        "servers",
        {},
    )

    if not isinstance(
        servers,
        dict,
    ):
        raise ValueError(
            "MCP servers 必须是对象"
        )

    result = []

    for name, item in servers.items():
        if not SAFE_NAME.fullmatch(
            name
        ):
            raise ValueError(
                f"非法 MCP 服务名称：{name}"
            )

        transport = item.get(
            "transport"
        )

        if transport not in {
            "stdio",
            "streamable_http",
        }:
            raise ValueError(
                f"MCP 服务 {name} "
                "的 transport 不受支持"
            )

        command = item.get(
            "command"
        )

        url = item.get(
            "url"
        )

        if (
            transport == "stdio"
            and not command
        ):
            raise ValueError(
                f"MCP 服务 {name} "
                "缺少 command"
            )

        if (
            transport == "streamable_http"
            and not url
        ):
            raise ValueError(
                f"MCP 服务 {name} "
                "缺少 url"
            )

        allowed_tools = item.get(
            "allowed_tools",
            [],
        )

        if not isinstance(
            allowed_tools,
            list,
        ):
            raise ValueError(
                "allowed_tools 必须是数组"
            )

        raw_risks = item.get(
            "tool_risks",
            {},
        )

        tool_risks = {
            tool_name: ToolRisk(
                risk
            )
            for tool_name, risk
            in raw_risks.items()
        }

        result.append(
            MCPServerConfig(
                name=name,
                transport=transport,
                command=command,
                args=tuple(
                    item.get(
                        "args",
                        [],
                    )
                ),
                url=url,
                env=dict(
                    item.get(
                        "env",
                        {},
                    )
                ),
                allowed_tools=frozenset(
                    allowed_tools
                ),
                tool_risks=tool_risks,
            )
        )

    return result
