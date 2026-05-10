"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("subdomain", sa.String(63), unique=True, nullable=False),
        sa.Column("container_config", sa.String, nullable=False),
        sa.Column("status", sa.Enum("provisioning", "active", "suspended", "deleted", name="clientstatus"), nullable=False, server_default="provisioning"),
        sa.Column("addons", postgresql.JSONB, nullable=False, server_default='{"geoip":false,"bot_filter":false,"gtm_js_proxy":true,"lib_proxy":false,"cookie_extension":false,"rate_limit_rps":50}'),
        sa.Column("dns_record_id", sa.String(255), nullable=True),
        sa.Column("server_container_id", sa.String(255), nullable=True),
        sa.Column("preview_container_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "custom_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("domain", sa.String(255), unique=True, nullable=False),
        sa.Column("status", sa.Enum("pending_dns", "pending_cert", "active", "failed", name="customdomainstatus"), nullable=False, server_default="pending_dns"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_custom_domains_client_id", "custom_domains", ["client_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_clients_status", "clients", ["status"])


def downgrade() -> None:
    op.drop_index("ix_clients_status", table_name="clients")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_custom_domains_client_id", table_name="custom_domains")
    op.drop_table("custom_domains")
    op.drop_table("clients")
    op.drop_table("api_keys")
    op.execute("DROP TYPE IF EXISTS clientstatus")
    op.execute("DROP TYPE IF EXISTS customdomainstatus")
