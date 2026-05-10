import pytest
import uuid
import bcrypt
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ApiKey

RAW_KEY = "domain-test-key"


async def seed_key(db: AsyncSession):
    hashed = bcrypt.hashpw(RAW_KEY.encode(), bcrypt.gensalt()).decode()
    db.add(ApiKey(id=uuid.uuid4(), name="test", key_hash=hashed))
    await db.commit()


def auth():
    return {"X-API-Key": RAW_KEY}


async def create_client(api_client: AsyncClient, subdomain: str) -> str:
    r = await api_client.post(
        "/api/v1/clients",
        json={"name": "Test", "subdomain": subdomain, "container_config": "dGVzdA=="},
        headers=auth(),
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_add_custom_domain(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    client_id = await create_client(api_client, "cust1")
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        response = await api_client.post(
            f"/api/v1/clients/{client_id}/custom-domain",
            json={"domain": "tracking.example.com"},
            headers=auth(),
        )
    assert response.status_code == 201
    data = response.json()
    assert data["domain"] == "tracking.example.com"
    assert data["status"] == "pending_dns"
    assert data["cname_target"] is not None


@pytest.mark.asyncio
async def test_add_custom_domain_duplicate(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    client_id = await create_client(api_client, "cust2")
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(f"/api/v1/clients/{client_id}/custom-domain", json={"domain": "dup.example.com"}, headers=auth())
        response = await api_client.post(f"/api/v1/clients/{client_id}/custom-domain", json={"domain": "dup2.example.com"}, headers=auth())
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_custom_domain_status(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    client_id = await create_client(api_client, "cust3")
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(f"/api/v1/clients/{client_id}/custom-domain", json={"domain": "status.example.com"}, headers=auth())
    response = await api_client.get(f"/api/v1/clients/{client_id}/custom-domain/status", headers=auth())
    assert response.status_code == 200
    assert response.json()["status"] == "pending_dns"


@pytest.mark.asyncio
async def test_remove_custom_domain(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    client_id = await create_client(api_client, "cust4")
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(f"/api/v1/clients/{client_id}/custom-domain", json={"domain": "remove.example.com"}, headers=auth())
    response = await api_client.delete(f"/api/v1/clients/{client_id}/custom-domain", headers=auth())
    assert response.status_code == 204
    status_resp = await api_client.get(f"/api/v1/clients/{client_id}/custom-domain/status", headers=auth())
    assert status_resp.status_code == 404
