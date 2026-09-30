"""Add knowledge base scope.

Revision ID: 0002_add_knowledge_base_scope
Revises: 0001_initial
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_knowledge_base_scope"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.drop_column(
        "knowledge_bases",
        "scope",
    )
