"""Add AD domains table and domain scope to variables and checklist groups.

Revision ID: 20260509_ad_domains
Revises: 20260222_public_id_assets
Create Date: 2026-05-09

"""
from alembic import op
import sqlalchemy as sa


revision = "20260509_ad_domains"
down_revision = "20260222_public_id_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_domains",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("netbios_name", sa.String(length=50), nullable=True),
        sa.Column("dc_ip", sa.String(length=45), nullable=True),
        sa.Column("dc_fqdn", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trust_type", sa.String(length=50), nullable=True),
        sa.Column("parent_domain_id", sa.Integer(), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_domain_id"], ["ad_domains.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ad_domains_public_id", "ad_domains", ["public_id"], unique=True)
    op.create_index("ix_ad_domains_project_id", "ad_domains", ["project_id"])
    op.create_index("ix_ad_domains_parent_domain_id", "ad_domains", ["parent_domain_id"])

    with op.batch_alter_table("project_variables", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ad_domain_id", sa.Integer(), nullable=True))

    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ad_domain_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("checklist_groups", schema=None) as batch_op:
        batch_op.drop_column("ad_domain_id")

    with op.batch_alter_table("project_variables", schema=None) as batch_op:
        batch_op.drop_column("ad_domain_id")

    op.drop_index("ix_ad_domains_parent_domain_id", table_name="ad_domains")
    op.drop_index("ix_ad_domains_project_id", table_name="ad_domains")
    op.drop_index("ix_ad_domains_public_id", table_name="ad_domains")
    op.drop_table("ad_domains")
