"""Add public_id to discovered_files, services, flows, flow_steps for sync.

Revision ID: 20260222_public_id_assets
Revises: 20260221_sync_peers
Create Date: 2026-02-22

"""
from alembic import op
import sqlalchemy as sa


revision = "20260222_public_id_assets"
down_revision = "20260221_sync_peers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill with SQLite randomblob to avoid async connection usage in Python loop.

    # discovered_files
    with op.batch_alter_table("discovered_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
    op.execute(sa.text(
        "UPDATE discovered_files SET public_id = lower(hex(randomblob(16))) WHERE public_id IS NULL"
    ))
    with op.batch_alter_table("discovered_files", schema=None) as batch_op:
        batch_op.alter_column("public_id", nullable=False)
    op.create_index("ix_discovered_files_public_id", "discovered_files", ["public_id"], unique=True)

    # services
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
    op.execute(sa.text(
        "UPDATE services SET public_id = lower(hex(randomblob(16))) WHERE public_id IS NULL"
    ))
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.alter_column("public_id", nullable=False)
    op.create_index("ix_services_public_id", "services", ["public_id"], unique=True)

    # flows
    with op.batch_alter_table("flows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
    op.execute(sa.text(
        "UPDATE flows SET public_id = lower(hex(randomblob(16))) WHERE public_id IS NULL"
    ))
    with op.batch_alter_table("flows", schema=None) as batch_op:
        batch_op.alter_column("public_id", nullable=False)
    op.create_index("ix_flows_public_id", "flows", ["public_id"], unique=True)

    # flow_steps
    with op.batch_alter_table("flow_steps", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
    op.execute(sa.text(
        "UPDATE flow_steps SET public_id = lower(hex(randomblob(16))) WHERE public_id IS NULL"
    ))
    with op.batch_alter_table("flow_steps", schema=None) as batch_op:
        batch_op.alter_column("public_id", nullable=False)
    op.create_index("ix_flow_steps_public_id", "flow_steps", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_flow_steps_public_id", table_name="flow_steps")
    with op.batch_alter_table("flow_steps", schema=None) as batch_op:
        batch_op.drop_column("public_id")

    op.drop_index("ix_flows_public_id", table_name="flows")
    with op.batch_alter_table("flows", schema=None) as batch_op:
        batch_op.drop_column("public_id")

    op.drop_index("ix_services_public_id", table_name="services")
    with op.batch_alter_table("services", schema=None) as batch_op:
        batch_op.drop_column("public_id")

    op.drop_index("ix_discovered_files_public_id", table_name="discovered_files")
    with op.batch_alter_table("discovered_files", schema=None) as batch_op:
        batch_op.drop_column("public_id")
