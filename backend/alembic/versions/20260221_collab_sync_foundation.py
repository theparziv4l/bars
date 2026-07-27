"""Collaboration and sync foundation.

Revision ID: 20260221_collab_sync
Revises: 20250221_target_filter
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_collab_sync"
down_revision = "20250221_target_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))

    with op.batch_alter_table("hosts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("checklist_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("executions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("started_by_user_id", sa.Integer(), nullable=True))

    with op.batch_alter_table("project_variables", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("public_id", name="uq_users_public_id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id", name="uq_project_members_public_id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )

    op.create_table(
        "checklist_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column("host_scope_key", sa.String(length=64), nullable=False),
        sa.Column("claimed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["checklist_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_id"], ["hosts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["claimed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("public_id", name="uq_checklist_claim_public_id"),
        sa.UniqueConstraint("project_id", "item_id", "host_scope_key", name="uq_checklist_claim_scope"),
    )

    op.create_table(
        "sync_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_public_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("logical_ts", sa.Integer(), nullable=False),
        sa.Column("source_node_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_sync_events_event_id"),
    )

    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("peer_node_id", sa.String(length=120), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("last_logical_ts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("peer_node_id", "direction", name="uq_sync_cursor_peer_direction"),
    )

    # Helpful indexes
    op.create_index("ix_users_public_id", "users", ["public_id"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_index("ix_project_members_project_user", "project_members", ["project_id", "user_id"], unique=False)
    op.create_index("ix_checklist_claims_scope_active", "checklist_claims", ["project_id", "item_id", "is_active"], unique=False)
    op.create_index("ix_sync_events_logical_ts", "sync_events", ["logical_ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sync_events_logical_ts", table_name="sync_events")
    op.drop_index("ix_checklist_claims_scope_active", table_name="checklist_claims")
    op.drop_index("ix_project_members_project_user", table_name="project_members")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_public_id", table_name="users")

    op.drop_table("sync_cursors")
    op.drop_table("sync_events")
    op.drop_table("checklist_claims")
    op.drop_table("project_members")
    op.drop_table("users")

    with op.batch_alter_table("project_variables", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("public_id")

    with op.batch_alter_table("executions", schema=None) as batch_op:
        batch_op.drop_column("started_by_user_id")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("public_id")

    with op.batch_alter_table("checklist_items", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("public_id")

    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("public_id")

    with op.batch_alter_table("hosts", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("public_id")

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("public_id")
