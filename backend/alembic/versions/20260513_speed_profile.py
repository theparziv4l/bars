"""Add speed_profile column to checklist_groups.

Revision ID: 20260513_speed_profile
Revises: 20260512_default_import_flags
Create Date: 2026-05-13

"""
from alembic import op
import sqlalchemy as sa


revision = "20260513_speed_profile"
down_revision = "20260512_default_import_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("checklist_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "speed_profile",
                sa.String(20),
                nullable=False,
                server_default=sa.text("'default'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("checklist_groups") as batch_op:
        batch_op.drop_column("speed_profile")
