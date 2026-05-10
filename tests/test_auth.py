import pytest
import bcrypt
import uuid
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ApiKey


async def create_api_key(db: AsyncSession, name: str, raw_key: str) -> str:
    hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    key = ApiKey(id=uuid.uuid4(), name=name, key_hash=hashed)
    db.add(key)
    await db.commit()
    return raw_key


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(api_client: AsyncClient):
    response = await api_client.get("/api/v1/clients")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(api_client: AsyncClient, db: AsyncSession):
    await create_api_key(db, "existing", "valid-key-xyz")
    response = await api_client.get("/api/v1/clients", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_api_key_passes(api_client: AsyncClient, db: AsyncSession):
    raw_key = "test-key-abc123"
    await create_api_key(db, "test", raw_key)
    response = await api_client.get("/api/v1/clients", headers={"X-API-Key": raw_key})
    assert response.status_code == 200
