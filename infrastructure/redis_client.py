"""
Redis 连接管理。

Redis 主要负责：
- Worker 任务通知；
- 取消标记；
- SSE 新事件通知；
- 临时限流和协调。

PostgreSQL 保存持久化业务状态。
"""

from functools import lru_cache

from redis.asyncio import Redis

from infrastructure.config import (
    get_settings,
)


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """创建可复用的 Redis 客户端。"""

    settings = get_settings()

    return Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
    )


async def close_redis() -> None:
    """关闭 Redis 连接。"""

    await get_redis().aclose()

    get_redis.cache_clear()
