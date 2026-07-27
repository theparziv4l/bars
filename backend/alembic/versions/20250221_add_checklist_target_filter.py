"""Add target_filter to checklist_items.

Revision ID: 20250221_target_filter
Revises: 20250221_host_tags
Create Date: 2025-02-21

"""
from alembic import op
import sqlalchemy as sa


revision = "20250221_target_filter"
down_revision = "20250221_host_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("checklist_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("target_filter", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("checklist_items", schema=None) as batch_op:
        batch_op.drop_column("target_filter")
