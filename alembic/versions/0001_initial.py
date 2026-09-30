"""
初始数据库迁移。

此版本对应 infrastructure/models.py 中
定义的全部业务表。

通过 ORM metadata 创建表，避免手工重复
维护数百行 Column 定义。

注意：
这是首次建库迁移，不适用于已经存在旧表的数据库。
"""

from alembic import op

from infrastructure.database import Base

import infrastructure.models  # noqa: F401


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    在当前迁移事务内创建初始表。

    sorted_tables 会按外键依赖顺序排列。
    """

    connection = op.get_bind()

    for table in Base.metadata.sorted_tables:
        table.create(
            bind=connection,
            checkfirst=False,
        )


def downgrade() -> None:
    """
    逆序删除初始表。

    此操作会删除业务数据，生产环境不得
    在没有备份和审批的情况下执行。
    """

    connection = op.get_bind()

    for table in reversed(
        Base.metadata.sorted_tables
    ):
        table.drop(
            bind=connection,
            checkfirst=False,
        )
