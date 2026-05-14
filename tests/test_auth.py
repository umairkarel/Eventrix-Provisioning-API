import pytest
import uuid
import bcrypt
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ApiKey, Tenant


async def create_tenant_and_key(db: AsyncSession, raw_key: str) -> tuple[Tenant, ApiKey]:
    tenant = Tenant(id=uuid.uuid4(), name="Auth Test Tenant")
    db.add(tenant)
    await db.flush()
    hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    key = ApiKey(id=uuid.uuid4(), tenant_id=tenant.id, name="test", key_hash=hashed)
    db.add(key)
    await db.commit()
    return tenant, key


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(api_client: AsyncClient):
    response = await api_client.get("/api/v1/servers")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(api_client: AsyncClient):
    response = await api_client.get("/api/v1/servers", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_api_key_passes(api_client: AsyncClient, db: AsyncSession):
    raw_key = "test-key-abc123"
    await create_tenant_and_key(db, raw_key)
    response = await api_client.get("/api/v1/servers", headers={"X-API-Key": raw_key})
    assert response.status_code == 200
