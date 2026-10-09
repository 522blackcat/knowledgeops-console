"""FastAPI 应用入口。"""

from contextlib import (
    asynccontextmanager,
)

from fastapi import FastAPI

from fastapi.responses import (
    JSONResponse,
)

from app.agent_api import (
    router as agent_router,
)

from app.admin_api import (
    router as admin_router,
)

from app.audit_api import (
    router as audit_router,
)

from app.approval_api import (
    router as approval_router,
)

from app.auth_api import (
    router as auth_router,
)

from app.run_api import (
    router as run_router,
)

from app.rag_eval_api import (
    router as rag_eval_router,
)

from app.knowledge_api import (
    router as knowledge_router,
)

from app.knowledge_version_api import (
    router as knowledge_version_router,
)

from app.knowledge_delete_api import (
    router as knowledge_delete_router,
)

from app.sse_api import (
    router as sse_router,
)

from infrastructure.database import (
    close_database,
)

from infrastructure.logging import (
    configure_logging,
    get_logger,
)

from infrastructure.redis_client import (
    close_redis,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期。

    Web 进程不执行 Agent 任务。
    Agent Worker 和 RAG Worker 独立运行。
    """

    configure_logging()

    logger = get_logger(
        "app.lifecycle"
    )

    logger.info(
        "web_started"
    )

    try:
        yield

    finally:
        await close_redis()
        await close_database()

        logger.info(
            "web_stopped"
        )


app = FastAPI(
    title="KnowledgeOps Console",
    version="1.0.0",
    lifespan=lifespan,
)


app.include_router(
    auth_router
)

app.include_router(
    agent_router
)

app.include_router(
    admin_router
)

app.include_router(
    audit_router
)

app.include_router(
    run_router
)

app.include_router(
    rag_eval_router
)

app.include_router(
    approval_router
)

app.include_router(
    sse_router
)

app.include_router(
    knowledge_router
)

app.include_router(
    knowledge_version_router
)

app.include_router(
    knowledge_delete_router
)


@app.get(
    "/health/live",
    tags=["健康检查"],
)
async def liveness():
    """进程存活检查。"""

    return JSONResponse({
        "status": "ok",
    })
