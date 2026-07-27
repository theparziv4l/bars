"""Add flow_executions table and flow_steps target columns.

Revision ID: 20260510_flow_executions
Revises: 20260509_ad_domains
Create Date: 2026-05-10

"""
from alembic import op
import sqlalchemy as sa


revision = "20260510_flow_executions"
down_revision = "20260509_ad_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("variables", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("targets", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_by_user_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["flow_id"], ["flows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_id"], ["hosts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_flow_executions_flow_id", "flow_executions", ["flow_id"])
    op.create_index("ix_flow_executions_status", "flow_executions", ["status"])

    with op.batch_alter_table("flow_steps") as batch_op:
        batch_op.add_column(sa.Column("target_mode", sa.String(length=20), nullable=False, server_default="inherit"))
        batch_op.add_column(sa.Column("target_filter", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("flow_steps") as batch_op:
        batch_op.drop_column("target_filter")
        batch_op.drop_column("target_mode")
    op.drop_index("ix_flow_executions_status", table_name="flow_executions")
    op.drop_index("ix_flow_executions_flow_id", table_name="flow_executions")
    op.drop_table("flow_executions")
