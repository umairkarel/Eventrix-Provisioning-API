import base64
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs
from pydantic import BaseModel, field_validator
from app.models import ServerStatus, CustomDomainStatus


def parse_container_config(container_config: str) -> tuple[str | None, int | None]:
    try:
        decoded = base64.b64decode(container_config).decode()
        params = parse_qs(decoded)
        container_id = params.get("id", [None])[0]
        env_str = params.get("env", [None])[0]
        env = int(env_str) if env_str is not None else None
        return container_id, env
    except Exception:
        return None, None


# ── Addons ────────────────────────────────────────────────────────────────────

class AddonsSchema(BaseModel):
    geoip: bool = False
    bot_filter: bool = False
    gtm_js_proxy: bool = True
    lib_proxy: bool = False
    cookie_extension: bool = False
    rate_limit_rps: int = 50


# ── Tenant ────────────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── API Key ───────────────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyResponse):
    """Returned only on creation — includes the raw key (shown once, never again)."""
    key: str


# ── Server ────────────────────────────────────────────────────────────────────

class ServerCreate(BaseModel):
    name: str
    subdomain: str
    container_config: str
    addons: Optional[AddonsSchema] = None

    @field_validator("subdomain")
    @classmethod
    def subdomain_valid(cls, v: str) -> str:
        import re
        if not re.match(r'^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$', v):
            raise ValueError("subdomain must be 2-63 chars, lowercase letters, digits, hyphens only")
        return v


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    container_config: Optional[str] = None
    addons: Optional[AddonsSchema] = None


class ServerResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    subdomain: str
    container_config: str
    gtm_container_id: Optional[str] = None
    gtm_env: Optional[int] = None
    status: ServerStatus
    addons: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Custom Domain ─────────────────────────────────────────────────────────────

class CustomDomainCreate(BaseModel):
    domain: str


class CustomDomainResponse(BaseModel):
    id: uuid.UUID
    server_id: uuid.UUID
    domain: str
    status: CustomDomainStatus
    verified_at: Optional[datetime]
    created_at: datetime
    cname_target: Optional[str] = None

    model_config = {"from_attributes": True}
