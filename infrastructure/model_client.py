"""
OpenAI 兼容模型客户端。

默认使用本地 Ollama：
    MODEL_PROVIDER=ollama

也支持配置云模型：
    MODEL_PROVIDER=cloud

两种路由都通过真实的
OpenAI-compatible Chat Completions API 调用。

注意：
模型本身必须支持工具调用，
仅有兼容 API 不代表模型具备 Function Calling 能力。
"""

from dataclasses import dataclass

import httpx

from openai import AsyncOpenAI

from infrastructure.config import (
    get_settings,
)


@dataclass(frozen=True)
class ModelRoute:
    """本次模型调用选中的路由。"""

    provider: str
    model: str
    base_url: str
    api_key: str


def select_model_route(
    requested_provider: str | None = None,
) -> ModelRoute:
    """
    选择服务端允许的模型路由。

    不接受客户端提交任意 base_url，
    避免将模型客户端变成 SSRF 代理。
    """

    settings = get_settings()

    provider = (
        requested_provider
        or settings.model_provider
    )

    if provider == "ollama":
        return ModelRoute(
            provider="ollama",
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            api_key=settings.ollama_api_key,
        )

    if provider == "cloud":
        if not all((
            settings.cloud_base_url,
            settings.cloud_model,
            settings.cloud_api_key,
        )):
            raise ValueError(
                "云模型配置不完整"
            )

        return ModelRoute(
            provider="cloud",
            model=settings.cloud_model,
            base_url=settings.cloud_base_url,
            api_key=settings.cloud_api_key,
        )

    raise ValueError(
        f"不支持的模型服务商：{provider}"
    )


class ModelClient:
    """
    统一管理异步模型客户端。

    使用示例：

        async with ModelClient() as model:
            response = await model.chat(
                messages=[...],
                tools=[...],
            )
    """

    def __init__(
        self,
        requested_provider: str | None = None,
    ):
        self.settings = get_settings()

        self.route = select_model_route(
            requested_provider
        )

        self.http_client: (
            httpx.AsyncClient | None
        ) = None

        self.client: (
            AsyncOpenAI | None
        ) = None

    async def __aenter__(self):
        """
        初始化真实 HTTP 客户端。

        本地 Ollama 默认不继承系统代理，
        避免 localhost 请求被代理转发。
        """

        self.http_client = (
            httpx.AsyncClient(
                timeout=(
                    self.settings
                    .model_timeout_seconds
                ),
                trust_env=(
                    self.route.provider
                    != "ollama"
                ),
            )
        )

        self.client = AsyncOpenAI(
            api_key=self.route.api_key,
            base_url=self.route.base_url,
            timeout=(
                self.settings
                .model_timeout_seconds
            ),
            max_retries=(
                self.settings
                .model_max_retries
            ),
            http_client=self.http_client,
        )

        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        """关闭模型客户端和 HTTP 连接池。"""

        if self.client is not None:
            await self.client.close()

        elif self.http_client is not None:
            await self.http_client.aclose()

        self.client = None
        self.http_client = None

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
    ):
        """
        发起一次真实模型调用。

        tools：
            OpenAI Function Calling 格式的工具列表。

        max_tokens：
            可选的最大输出 Token 数。

        异常直接交给上层处理，
        不将模型失败伪装成正常回答。
        """

        if self.client is None:
            raise RuntimeError(
                "请通过 async with "
                "ModelClient() 使用客户端"
            )

        arguments = {
            "model": self.route.model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            if max_tokens <= 0:
                raise ValueError(
                    "max_tokens 必须大于零"
                )

            arguments["max_tokens"] = (
                max_tokens
            )

        if tools:
            arguments["tools"] = tools
            arguments["tool_choice"] = (
                tool_choice
            )

        return await (
            self.client
            .chat
            .completions
            .create(**arguments)
        )
