import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON, Enum as SAEnum, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ServerStatus(str, enum.Enum):
    provisioning = "provisioning"
    active = "active"
    suspended = "suspended"
    error = "error"
    deleted = "deleted"


class CustomDomainStatus(str, enum.Enum):
    pending_dns = "pending_dns"
    pending_cert = "pending_cert"
    active = "active"
    failed = "failed"


def _default_addons() -> dict:
    return {
        "geoip": False,
        "bot_filter": False,
        "gtm_js_proxy": True,
        "lib_proxy": False,
        "cookie_extension": False,
        "rate_limit_rps": 50,
    }


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    servers: Mapped[list["Server"]] = relationship(back_populates="tenant")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="tenant")


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (
        Index(
            "uq_servers_subdomain_active",
            "subdomain",
            unique=True,
            postgresql_where=text("status != 'deleted'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subdomain: Mapped[str] = mapped_column(String(63), nullable=False)
    container_config: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[ServerStatus] = mapped_column(
        SAEnum(ServerStatus, name="serverstatus"), default=ServerStatus.provisioning
    )
    addons: Mapped[dict] = mapped_column(JSON, default=_default_addons)
    gtm_container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gtm_env: Mapped[int | None] = mapped_column(nullable=True)
    dns_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_container_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preview_container_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="servers")
    custom_domain: Mapped["CustomDomain"] = relationship(back_populates="server", uselist=False)


class CustomDomain(Base):
    __tablename__ = "custom_domains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("servers.id"), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[CustomDomainStatus] = mapped_column(SAEnum(CustomDomainStatus), default=CustomDomainStatus.pending_dns)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    server: Mapped["Server"] = relationship(back_populates="custom_domain")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="api_keys")
