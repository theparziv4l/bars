"""Add ad_domain_id to flows and paused_at_step to flow_executions.

Revision ID: 20260513_flow_ad_domain
Revises: 20260513_speed_profile
Create Date: 2026-05-13

"""
from alembic import op
import sqlalchemy as sa


revision = "20260513_flow_ad_domain"
down_revision = "20260513_speed_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("flows") as batch_op:
        batch_op.add_column(sa.Column("ad_domain_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_flows_ad_domain_id", ["ad_domain_id"])
        batch_op.create_foreign_key(
            "fk_flows_ad_domain_id",
            "ad_domains",
            ["ad_domain_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("flow_executions") as batch_op:
        batch_op.add_column(sa.Column("paused_at_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("flow_executions") as batch_op:
        batch_op.drop_column("paused_at_step")

    with op.batch_alter_table("flows") as batch_op:
        batch_op.drop_constraint("fk_flows_ad_domain_id", type_="foreignkey")
        batch_op.drop_index("ix_flows_ad_domain_id")
        batch_op.drop_column("ad_domain_id")
