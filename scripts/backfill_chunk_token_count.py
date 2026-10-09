"""Backfill stored document chunk token counts."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.database import session_scope
from infrastructure.models import DocumentChunk
from rag.tokenizer import token_length


async def backfill(
    *,
    dry_run: bool,
    limit: int | None,
) -> int:
    statement = (
        select(DocumentChunk)
        .where(DocumentChunk.token_count == 0)
        .order_by(
            DocumentChunk.document_id.asc(),
            DocumentChunk.document_version.asc(),
            DocumentChunk.chunk_index.asc(),
        )
    )

    if limit is not None:
        statement = statement.limit(limit)

    updated = 0

    async with session_scope() as db:
        result = await db.execute(statement)
        chunks = result.scalars().all()

        for chunk in chunks:
            computed = token_length(chunk.text or "")

            if computed <= 0:
                continue

            updated += 1

            if not dry_run:
                chunk.token_count = computed

    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill document_chunks.token_count "
            "from stored chunk text."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be updated without writing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of zero-token chunks to inspect.",
    )

    args = parser.parse_args()

    updated = asyncio.run(
        backfill(
            dry_run=args.dry_run,
            limit=args.limit,
        )
    )

    action = "would update" if args.dry_run else "updated"
    print(f"token_count backfill {action} {updated} chunks.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
