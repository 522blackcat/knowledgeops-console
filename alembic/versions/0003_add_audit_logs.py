"""Add audit logs.

Revision ID: 0003_add_audit_logs
Revises: 0002_add_knowledge_base_scope
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_add_audit_logs"
down_revision = "0002_add_knowledge_base_scope"
branch_labels = None
depends_on = None


TABLE_NAME = "audit_logs"


def _table_exists() -> bool:
    inspector = sa.inspect(op.get_bind())
    return TABLE_NAME in inspector.get_table_names()


def upgrade() -> None:
    if _table_exists():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "actor_username",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "resource_type",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "summary",
            sa.String(length=500),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_logs_tenant_id",
        TABLE_NAME,
        ["tenant_id"],
    )
    op.create_index(
        "ix_audit_logs_actor_user_id",
        TABLE_NAME,
        ["actor_user_id"],
    )
    op.create_index(
        "ix_audit_tenant_created",
        TABLE_NAME,
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_audit_tenant_resource",
        TABLE_NAME,
        ["tenant_id", "resource_type", "resource_id"],
    )


def downgrade() -> None:
    if not _table_exists():
        return

    op.drop_index(
        "ix_audit_tenant_resource",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_audit_tenant_created",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_audit_logs_actor_user_id",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_audit_logs_tenant_id",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
