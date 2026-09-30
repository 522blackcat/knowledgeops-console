"""
LangGraph PostgreSQL Checkpoint。

与业务 ORM 数据库连接分离：
    ORM：
        postgresql+asyncpg://...

    LangGraph Checkpoint：
        postgresql://...

首次部署需要初始化 Checkpoint 表。

注意：
setup() 是结构初始化操作，
不应在每次 HTTP 请求中调用。
"""

from contextlib import (
    asynccontextmanager,
)

from langgraph.checkpoint.postgres.aio import (
    AsyncPostgresSaver,
)

from infrastructure.config import (
    get_settings,
)


@asynccontextmanager
async def checkpoint_saver():
    """
    为 Worker 提供异步 Checkpoint Saver。

    Worker 应在同一任务的图执行期间
    保持上下文有效。
    """

    settings = get_settings()

    async with AsyncPostgresSaver.from_conn_string(
        settings.checkpoint_database_url
    ) as saver:
        yield saver


async def setup_checkpoint_tables() -> None:
    """
    首次部署时初始化 LangGraph Checkpoint 表。

    可由独立管理命令调用。
    """

    async with checkpoint_saver() as saver:
        await saver.setup()
