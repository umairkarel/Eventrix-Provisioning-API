"""multi-tenant refactor: add Tenant, rename Client→Server

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    # 1. Create tenants table
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # 2. Seed a default tenant so existing rows get a valid FK target
    op.execute(
        f"INSERT INTO tenants (id, name) VALUES ('{DEFAULT_TENANT_ID}', 'Default Tenant')"
    )

    # 3. Add tenant_id to clients (nullable first so existing rows don't violate NOT NULL)
    op.add_column(
        "clients",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(f"UPDATE clients SET tenant_id = '{DEFAULT_TENANT_ID}'")
    op.alter_column("clients", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_clients_tenant_id", "clients", "tenants", ["tenant_id"], ["id"]
    )

    # 4. Add tenant_id to api_keys
    op.add_column(
        "api_keys",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(f"UPDATE api_keys SET tenant_id = '{DEFAULT_TENANT_ID}'")
    op.alter_column("api_keys", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_api_keys_tenant_id", "api_keys", "tenants", ["tenant_id"], ["id"]
    )

    # 5. Rename the PostgreSQL enum type before renaming the table
    op.execute("ALTER TYPE clientstatus RENAME TO serverstatus")

    # 6. Rename clients → servers
    op.rename_table("clients", "servers")

    # 7. Rename client_id → server_id in custom_domains
    op.drop_constraint(
        "custom_domains_client_id_fkey", "custom_domains", type_="foreignkey"
    )
    op.drop_index("ix_custom_domains_client_id", table_name="custom_domains")
    op.alter_column("custom_domains", "client_id", new_column_name="server_id")
    op.create_foreign_key(
        "custom_domains_server_id_fkey",
        "custom_domains",
        "servers",
        ["server_id"],
        ["id"],
    )
    op.create_index("ix_custom_domains_server_id", "custom_domains", ["server_id"])

    # 8. Rename the partial unique index (was on clients, now on servers after rename)
    op.drop_index("uq_clients_subdomain_active", table_name="servers")
    op.execute(
        "CREATE UNIQUE INDEX uq_servers_subdomain_active ON servers(subdomain) "
        "WHERE status != 'deleted'"
    )


def downgrade() -> None:
    # 8. Drop new index, restore old one
    op.execute("DROP INDEX IF EXISTS uq_servers_subdomain_active")

    # 7. Restore custom_domains column name and FK (still on servers table at this point)
    op.drop_index("ix_custom_domains_server_id", table_name="custom_domains")
    op.drop_constraint(
        "custom_domains_server_id_fkey", "custom_domains", type_="foreignkey"
    )
    op.alter_column("custom_domains", "server_id", new_column_name="client_id")

    # 6. Rename servers → clients
    op.rename_table("servers", "clients")

    # 7 cont. Re-create FK/index pointing to clients now
    op.create_foreign_key(
        "custom_domains_client_id_fkey",
        "custom_domains",
        "clients",
        ["client_id"],
        ["id"],
    )
    op.create_index("ix_custom_domains_client_id", "custom_domains", ["client_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_clients_subdomain_active ON clients(subdomain) "
        "WHERE status != 'deleted'"
    )

    # 5. Rename enum back
    op.execute("ALTER TYPE serverstatus RENAME TO clientstatus")

    # 4. Remove tenant_id from api_keys
    op.drop_constraint("fk_api_keys_tenant_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "tenant_id")

    # 3. Remove tenant_id from clients
    op.drop_constraint("fk_clients_tenant_id", "clients", type_="foreignkey")
    op.drop_column("clients", "tenant_id")

    # 1. Drop tenants table
    op.drop_table("tenants")
