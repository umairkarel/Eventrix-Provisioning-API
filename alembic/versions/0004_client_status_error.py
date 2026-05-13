"""add error value to clientstatus enum

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE clientstatus ADD VALUE IF NOT EXISTS 'error'")


def downgrade() -> None:
    # Postgres does not support removing enum values; migrate affected rows first
    op.execute("UPDATE clients SET status = 'suspended' WHERE status = 'error'")
