"""Queue rebuild ingest jobs for existing knowledge documents."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.database import session_scope
from infrastructure.models import IngestJob, KnowledgeDocument
from infrastructure.redis_client import close_redis, get_redis
from rag.storage import resolve_storage_path


ACTIVE_JOB_STATUSES = ("queued", "processing")


async def load_documents(
    *,
    document_id: uuid.UUID | None,
    all_ready: bool,
    limit: int | None,
) -> list[KnowledgeDocument]:
    statement = select(KnowledgeDocument).where(
        KnowledgeDocument.deleted_at.is_(None)
    )

    if document_id is not None:
        statement = statement.where(
            KnowledgeDocument.id == document_id
        )
    elif all_ready:
        statement = statement.where(
            KnowledgeDocument.status == "ready"
        )
    else:
        raise ValueError(
            "Use --document-id or --all-ready."
        )

    statement = statement.order_by(
        KnowledgeDocument.created_at.asc()
    )

    if limit is not None:
        statement = statement.limit(limit)

    async with session_scope() as db:
        result = await db.execute(statement)
        return list(result.scalars().all())


async def queue_reindex(
    *,
    document_id: uuid.UUID | None,
    all_ready: bool,
    dry_run: bool,
    limit: int | None,
) -> list[dict]:
    documents = await load_documents(
        document_id=document_id,
        all_ready=all_ready,
        limit=limit,
    )

    queued: list[dict] = []

    async with session_scope() as db:
        for candidate in documents:
            document = await db.scalar(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.id
                    == candidate.id,
                    KnowledgeDocument.deleted_at.is_(None),
                )
                .with_for_update()
            )

            if document is None:
                continue

            active_job = await db.scalar(
                select(IngestJob)
                .where(
                    IngestJob.document_id
                    == document.id,
                    IngestJob.status.in_(
                        ACTIVE_JOB_STATUSES
                    ),
                )
                .limit(1)
            )

            if active_job is not None:
                queued.append({
                    "document_id": str(document.id),
                    "filename": document.filename,
                    "status": "skipped_active_job",
                    "active_job_id": str(active_job.id),
                })
                continue

            storage_path = resolve_storage_path(
                document.storage_path
            )

            if not storage_path.exists():
                queued.append({
                    "document_id": str(document.id),
                    "filename": document.filename,
                    "status": "skipped_missing_file",
                    "storage_path": document.storage_path,
                })
                continue

            next_version = (
                document.current_version + 1
            )

            entry = {
                "document_id": str(document.id),
                "filename": document.filename,
                "status": (
                    "would_queue"
                    if dry_run
                    else "queued"
                ),
                "document_version": next_version,
            }

            if not dry_run:
                job = IngestJob(
                    tenant_id=document.tenant_id,
                    document_id=document.id,
                    document_version=next_version,
                    storage_path=document.storage_path,
                    content_hash=document.content_hash,
                    filename=document.filename,
                    status="queued",
                )
                db.add(job)
                await db.flush()
                entry["ingest_job_id"] = str(job.id)

            queued.append(entry)

    if not dry_run:
        redis = get_redis()

        try:
            for item in queued:
                job_id = item.get("ingest_job_id")
                if job_id:
                    await redis.publish(
                        "rag:ingest:wakeup",
                        job_id,
                    )
        finally:
            await close_redis()

    return queued


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Queue reindex jobs for existing ready knowledge documents."
        )
    )
    parser.add_argument(
        "--document-id",
        type=uuid.UUID,
        default=None,
        help="Only rebuild one document.",
    )
    parser.add_argument(
        "--all-ready",
        action="store_true",
        help="Queue all ready, non-deleted documents.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned jobs without writing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum documents to inspect.",
    )

    args = parser.parse_args()

    try:
        queued = asyncio.run(
            queue_reindex(
                document_id=args.document_id,
                all_ready=args.all_ready,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        )
    except ValueError as exc:
        print(str(exc))
        return 2

    print(
        f"reindex {'previewed' if args.dry_run else 'queued'} "
        f"{len(queued)} documents."
    )

    for item in queued:
        print(
            f"- {item['status']}: {item['filename']} "
            f"document={item['document_id']} "
            f"version={item.get('document_version', '-')}"
            + (
                f" job={item['ingest_job_id']}"
                if item.get("ingest_job_id")
                else ""
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
