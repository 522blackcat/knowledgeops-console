"""
全局配置模块。

所有可调参数从环境变量读取。

本地开发：
    knowledgeops-console/.env

生产环境：
    由部署平台注入环境变量。

不在代码中写入真实密码或 API Key。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)


class Settings(BaseSettings):
    """项目统一配置。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------
    # Web 服务
    # ------------------------------------------------------

    app_name: str = "KnowledgeOps Console"

    app_env: Literal[
        "development",
        "staging",
        "production",
    ] = "development"

    app_host: str = "127.0.0.1"

    app_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    app_public_url: str = (
        "http://127.0.0.1:8000"
    )

    app_timezone: str = "Asia/Shanghai"

    session_secret: str = ""

    bootstrap_admin_username: str = (
        "admin"
    )

    bootstrap_admin_password: str = ""

    # ------------------------------------------------------
    # 数据库与队列
    # ------------------------------------------------------

    database_url: str = (
        "postgresql+asyncpg://agent:change-me"
        "@127.0.0.1:5432/agent"
    )

    checkpoint_database_url: str = (
        "postgresql://agent:change-me"
        "@127.0.0.1:5432/agent"
    )

    redis_url: str = (
        "redis://127.0.0.1:6379/0"
    )

    qdrant_url: str = (
        "http://127.0.0.1:6333"
    )

    qdrant_api_key: str = ""

    # ------------------------------------------------------
    # 模型路由
    # ------------------------------------------------------

    model_provider: Literal[
        "ollama",
        "cloud",
    ] = "ollama"

    ollama_base_url: str = (
        "http://127.0.0.1:11434/v1"
    )

    ollama_model: str = "qwen3:1.7b"

    ollama_api_key: str = "ollama"

    cloud_base_url: str = ""

    cloud_model: str = ""

    cloud_api_key: str = ""

    model_timeout_seconds: float = Field(
        default=120,
        gt=0,
    )

    model_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
    )

    model_max_rounds: int = Field(
        default=12,
        ge=1,
        le=100,
    )

    model_max_tool_calls: int = Field(
        default=16,
        ge=0,
        le=100,
    )

    # ------------------------------------------------------
    # RAG
    # ------------------------------------------------------

    embedding_model: str = (
        "BAAI/bge-m3"
    )

    reranker_model: str = (
        "BAAI/bge-reranker-v2-m3"
    )

    rag_model_local_files_only: bool = True

    rag_retrieval_url: str = ""

    rag_collection_prefix: str = (
        "agent_knowledge"
    )

    # 分块预算单位为 Token，由 Embedding 模型的 tokenizer 计数。
    rag_chunk_size_tokens: int = Field(
        default=500,
        ge=100,
    )

    rag_chunk_overlap_tokens: int = Field(
        default=50,
        ge=0,
    )

    rag_embedding_batch_size: int = Field(
        default=16,
        ge=1,
    )

    rag_qdrant_upsert_batch_size: int = Field(
        default=128,
        ge=1,
    )

    # ------------------------------
    # 检索漏斗
    # ------------------------------

    rag_vector_limit: int = Field(
        default=30,
        ge=1,
    )

    rag_bm25_limit: int = Field(
        default=30,
        ge=1,
    )

    rag_rerank_limit: int = Field(
        default=12,
        ge=1,
    )

    rag_final_limit: int = Field(
        default=8,
        ge=1,
    )

    rag_context_top_k: int = Field(
        default=5,
        ge=1,
    )

    rag_display_top_k: int = Field(
        default=8,
        ge=1,
    )

    rag_rrf_k: int = Field(
        default=60,
        ge=1,
    )

    rag_bm25_max_chunks: int = Field(
        default=20000,
        ge=1,
    )

    # 为空表示不做分数截断。
    # 仅在启用 Reranker 时按交叉编码器分数生效。
    rag_min_score: float | None = 0.35

    rag_relative_score_ratio: float = Field(
        default=0.45,
        ge=0,
        le=1,
    )

    rag_retrieval_mode: Literal[
        "auto",
        "always",
        "never",
    ] = "auto"

    qdrant_timeout_seconds: float = Field(
        default=30,
        gt=0,
    )

    ingest_worker_concurrency: int = Field(
        default=2,
        ge=1,
    )

    ingest_max_retries: int = Field(
        default=5,
        ge=0,
    )

    ingest_lease_seconds: int = Field(
        default=600,
        ge=30,
    )

    ingest_queue_name: str = (
        "rag:ingest"
    )

    # ------------------------------------------------------
    # Agent Worker
    # ------------------------------------------------------

    agent_queue_name: str = (
        "agent:runs"
    )

    agent_worker_concurrency: int = Field(
        default=2,
        ge=1,
        le=32,
    )

    agent_lease_seconds: int = Field(
        default=900,
        ge=30,
    )

    # ------------------------------------------------------
    # 记忆
    # ------------------------------------------------------

    memory_short_term_token_budget: int = Field(
        default=6000,
        ge=256,
    )

    memory_summary_trigger_tokens: int = Field(
        default=4500,
        ge=128,
    )

    memory_summary_token_budget: int = Field(
        default=1200,
        ge=128,
    )

    memory_default_ttl_days: int = Field(
        default=0,
        ge=0,
    )

    # ------------------------------------------------------
    # MCP
    # ------------------------------------------------------

    mcp_config_path: str = (
        "mcp_servers.json"
    )

    mcp_connect_timeout_seconds: float = Field(
        default=20,
        gt=0,
    )

    mcp_tool_timeout_seconds: float = Field(
        default=60,
        gt=0,
    )

    # ------------------------------------------------------
    # 安全与运行限制
    # ------------------------------------------------------

    access_token_expire_minutes: int = Field(
        default=60,
        ge=1,
    )

    run_timeout_seconds: int = Field(
        default=600,
        ge=1,
    )

    max_upload_bytes: int = Field(
        default=104857600,
        ge=1024,
    )

    max_question_length: int = Field(
        default=12000,
        ge=1,
    )

    # ------------------------------------------------------
    # 可观测性
    # ------------------------------------------------------

    log_level: str = "INFO"

    otel_enabled: bool = False

    otel_service_name: str = (
        "knowledgeops-console"
    )

    otel_exporter_otlp_endpoint: str = (
        "http://127.0.0.1:4317"
    )

    @model_validator(mode="after")
    def validate_settings(self):
        """启动前校验关键配置。"""

        if (
            self.rag_chunk_overlap_tokens
            >= self.rag_chunk_size_tokens
        ):
            raise ValueError(
                "RAG_CHUNK_OVERLAP_TOKENS 必须小于 "
                "RAG_CHUNK_SIZE_TOKENS"
            )

        if (
            self.rag_final_limit
            > self.rag_rerank_limit
        ):
            raise ValueError(
                "RAG_FINAL_LIMIT 不能大于 "
                "RAG_RERANK_LIMIT，"
                "否则重排候选不足，"
                "返回条数会静默缩水"
            )

        if self.rag_context_top_k > self.rag_final_limit:
            raise ValueError(
                "RAG_CONTEXT_TOP_K 不能大于 "
                "RAG_FINAL_LIMIT"
            )

        if self.rag_display_top_k > self.rag_final_limit:
            raise ValueError(
                "RAG_DISPLAY_TOP_K 不能大于 "
                "RAG_FINAL_LIMIT"
            )

        if (
            self.rag_rerank_limit
            > (
                self.rag_vector_limit
                + self.rag_bm25_limit
            )
        ):
            raise ValueError(
                "RAG_RERANK_LIMIT 超过两路候选之和，"
                "多出的名额没有实际候选"
            )

        if (
            self.memory_summary_trigger_tokens
            >= self.memory_short_term_token_budget
        ):
            raise ValueError(
                "摘要触发阈值必须小于"
                "短期记忆 Token 预算"
            )

        if (
            self.memory_summary_token_budget
            >= self.memory_short_term_token_budget
        ):
            raise ValueError(
                "摘要 Token 预算必须小于"
                "短期记忆 Token 预算"
            )

        if (
            self.agent_lease_seconds
            <= self.run_timeout_seconds
        ):
            raise ValueError(
                "AGENT_LEASE_SECONDS 必须大于 "
                "RUN_TIMEOUT_SECONDS"
            )

        if self.model_provider == "cloud":
            if not all((
                self.cloud_base_url,
                self.cloud_model,
                self.cloud_api_key,
            )):
                raise ValueError(
                    "使用云模型时必须配置 "
                    "CLOUD_BASE_URL、"
                    "CLOUD_MODEL 和 "
                    "CLOUD_API_KEY"
                )

        if self.app_env == "production":
            if len(self.session_secret) < 32:
                raise ValueError(
                    "生产环境 SESSION_SECRET "
                    "至少需要 32 个字符"
                )

            if (
                self.bootstrap_admin_password
            ):
                raise ValueError(
                    "生产环境禁止使用"
                    "BOOTSTRAP_ADMIN_PASSWORD "
                    "自动创建管理员"
                )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    同一进程只创建一次配置对象。

    如需在管理命令中重新读取环境变量，
    可以调用 get_settings.cache_clear()。
    """

    return Settings()
