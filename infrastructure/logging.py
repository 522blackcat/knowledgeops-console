"""
结构化日志。

使用 JSON 输出，便于 Docker 日志收集、
OpenTelemetry 和后续集中检索。

不应记录：
    密码、API Key、Authorization、
    Cookie、完整用户问题或工具密钥。
"""

import logging
import sys

import structlog

from infrastructure.config import (
    get_settings,
)


_configured = False


def configure_logging() -> None:
    """配置一次全局日志。"""

    global _configured

    if _configured:
        return

    settings = get_settings()

    level_name = (
        settings.log_level.upper()
    )

    level = getattr(
        logging,
        level_name,
        logging.INFO,
    )

    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(message)s",
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(
                fmt="iso",
                utc=True,
            ),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(
                ensure_ascii=False,
            ),
        ],
        wrapper_class=(
            structlog.make_filtering_bound_logger(
                level
            )
        ),
        logger_factory=(
            structlog.PrintLoggerFactory(
                file=sys.stdout
            )
        ),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str):
    """获取带模块名称的结构化日志。"""

    configure_logging()

    return structlog.get_logger(
        name
    )
