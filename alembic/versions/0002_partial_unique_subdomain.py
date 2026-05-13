"""partial unique index on subdomain for non-deleted clients

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("clients_subdomain_key", "clients", type_="unique")
    op.create_index(
        "uq_clients_subdomain_active",
        "clients",
        ["subdomain"],
        unique=True,
        postgresql_where="status != 'deleted'",
    )


def downgrade() -> None:
    op.drop_index("uq_clients_subdomain_active", table_name="clients")
    op.create_unique_constraint("clients_subdomain_key", "clients", ["subdomain"])
