"""Add knowledge base scope.

Revision ID: 0002_add_knowledge_base_scope
Revises: 0001_initial
Create Date: 2026-09-28

0001_initial 是按当前 ORM metadata 建表的，
所以新库里 scope 已经随 0001 一起建出来了，
这里再无条件 ADD COLUMN 会 DuplicateColumn 把整条升级事务打回，
全新部署永远到不了 head。先探测列在不在，
只对真正缺列的历史库补列。
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_knowledge_base_scope"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


KNOWLEDGE_BASES_TABLE = "knowledge_bases"


def _scope_column_exists() -> bool:
    inspector = sa.inspect(op.get_bind())

    if KNOWLEDGE_BASES_TABLE not in inspector.get_table_names():
        return False

    return any(
        column["name"] == "scope"
        for column in inspector.get_columns(KNOWLEDGE_BASES_TABLE)
    )


def upgrade() -> None:
    if _scope_column_exists():
        return

    op.add_column(
        "knowledge_bases",
        sa.Column(
            "scope",
            sa.String(length=50),
            nullable=False,
            server_default="general",
        ),
    )
    op.alter_column(
        "knowledge_bases",
        "scope",
        server_default=None,
    )


def downgrade() -> None:
    if not _scope_column_exists():
        return

    op.drop_column(
        "knowledge_bases",
        "scope",
    )
