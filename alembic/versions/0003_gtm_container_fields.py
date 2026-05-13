"""add gtm_container_id and gtm_env to clients

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("gtm_container_id", sa.String(64), nullable=True))
    op.add_column("clients", sa.Column("gtm_env", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "gtm_env")
    op.drop_column("clients", "gtm_container_id")
