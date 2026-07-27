"""Add sync_peers table for dynamic in-app peer configuration.

Revision ID: 20260221_sync_peers
Revises: 20260221_collab_sync
Create Date: 2026-02-21

"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_sync_peers"
down_revision = "20260221_collab_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_peers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("base_url", name="uq_sync_peers_base_url"),
    )
    op.create_index("ix_sync_peers_base_url", "sync_peers", ["base_url"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sync_peers_base_url", table_name="sync_peers")
    op.drop_table("sync_peers")
