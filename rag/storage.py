"""
RAG 上传文件存储。

文件使用随机 UUID 命名，不直接使用用户文件名
作为磁盘路径，避免路径穿越和文件名冲突。

PostgreSQL 中的 storage_path 保存服务端生成的
相对路径，不保存客户端提交的绝对路径。
"""

import hashlib
import uuid

from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from infrastructure.config import (
    get_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from rag.parser import (
    SUPPORTED_SUFFIXES,
)


UPLOAD_ROOT = (
    PROJECT_ROOT / "uploads"
).resolve()


@dataclass(frozen=True)
class StoredUpload:
    storage_path: str
    filename: str
    content_hash: str
    size_bytes: int


def resolve_storage_path(
    storage_path: str,
) -> Path:
    """
    将数据库中的相对路径解析到 uploads 目录。

    禁止绝对路径和越界路径。
    """

    candidate = Path(
        storage_path
    )

    if candidate.is_absolute():
        raise ValueError(
            "不允许使用绝对存储路径"
        )

    resolved = (
        UPLOAD_ROOT / candidate
    ).resolve()

    if not resolved.is_relative_to(
        UPLOAD_ROOT
    ):
        raise ValueError(
            "存储路径超出上传目录"
        )

    return resolved


async def save_upload(
    upload: UploadFile,
    *,
    tenant_id: uuid.UUID,
) -> StoredUpload:
    """
    流式保存上传文件并计算 SHA-256。

    不将整个文件一次性读入内存。
    """

    settings = get_settings()

    original_name = Path(
        upload.filename or ""
    ).name

    suffix = Path(
        original_name
    ).suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"不支持的文件类型：{suffix}"
        )

    relative_path = (
        Path(str(tenant_id))
        / f"{uuid.uuid4().hex}{suffix}"
    )

    destination = resolve_storage_path(
        str(relative_path)
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    digest = hashlib.sha256()
    total_bytes = 0

    try:
        with destination.open(
            "xb"
        ) as output:
            while True:
                block = await upload.read(
                    1024 * 1024
                )

                if not block:
                    break

                total_bytes += len(block)

                if total_bytes > (
                    settings.max_upload_bytes
                ):
                    raise ValueError(
                        "上传文件超过大小限制"
                    )

                digest.update(block)
                output.write(block)

        if total_bytes == 0:
            raise ValueError(
                "不允许上传空文件"
            )

    except BaseException:
        destination.unlink(
            missing_ok=True
        )
        raise

    finally:
        await upload.close()

    return StoredUpload(
        storage_path=(
            relative_path.as_posix()
        ),
        filename=original_name,
        content_hash=digest.hexdigest(),
        size_bytes=total_bytes,
    )
