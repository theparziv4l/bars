"""Add is_default_import and requires_auth flags to templates.

Revision ID: 20260512_default_import_flags
Revises: 20260510_flow_executions
Create Date: 2026-05-12

"""
from alembic import op
import sqlalchemy as sa


revision = "20260512_default_import_flags"
down_revision = "20260510_flow_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_default_import", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("requires_auth", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )

    with op.batch_alter_table("flows", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_default_import", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("requires_auth", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )

    with op.batch_alter_table("library_variables", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_default_import", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("library_variables", schema=None) as batch_op:
        batch_op.drop_column("is_default_import")

    with op.batch_alter_table("flows", schema=None) as batch_op:
        batch_op.drop_column("requires_auth")
        batch_op.drop_column("is_default_import")

    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.drop_column("requires_auth")
        batch_op.drop_column("is_default_import")
