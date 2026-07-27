"""Add tags to hosts table.

Revision ID: 20250221_host_tags
Revises:
Create Date: 2025-02-21

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250221_host_tags"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not support "add column with default" easily in one step for JSON.
    # Use batch mode for SQLite if needed.
    with op.batch_alter_table("hosts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tags", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("hosts", schema=None) as batch_op:
        batch_op.drop_column("tags")
