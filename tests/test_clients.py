import pytest
import uuid
import bcrypt
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ApiKey

RAW_KEY = "crud-test-key"


async def seed_key(db: AsyncSession):
    hashed = bcrypt.hashpw(RAW_KEY.encode(), bcrypt.gensalt()).decode()
    db.add(ApiKey(id=uuid.uuid4(), name="test", key_hash=hashed))
    await db.commit()


def auth():
    return {"X-API-Key": RAW_KEY}


@pytest.mark.asyncio
async def test_create_client(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    payload = {
        "name": "Acme Corp",
        "subdomain": "acme",
        "container_config": "dGVzdA==",
    }
    response = await api_client.post("/api/v1/clients", json=payload, headers=auth())
    assert response.status_code == 201
    data = response.json()
    assert data["subdomain"] == "acme"
    assert data["status"] == "provisioning"
    assert data["addons"]["rate_limit_rps"] == 50


@pytest.mark.asyncio
async def test_create_client_duplicate_subdomain(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    payload = {"name": "A", "subdomain": "dup", "container_config": "dGVzdA=="}
    await api_client.post("/api/v1/clients", json=payload, headers=auth())
    response = await api_client.post("/api/v1/clients", json=payload, headers=auth())
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_list_clients(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    await api_client.post("/api/v1/clients", json={"name": "A", "subdomain": "aaa", "container_config": "dGVzdA=="}, headers=auth())
    response = await api_client.get("/api/v1/clients", headers=auth())
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_client(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "B", "subdomain": "bbb", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    response = await api_client.get(f"/api/v1/clients/{client_id}", headers=auth())
    assert response.status_code == 200
    assert response.json()["id"] == client_id


@pytest.mark.asyncio
async def test_get_client_not_found(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    response = await api_client.get(f"/api/v1/clients/{uuid.uuid4()}", headers=auth())
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_client_addons(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "C", "subdomain": "ccc", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    response = await api_client.patch(
        f"/api/v1/clients/{client_id}",
        json={"addons": {"geoip": True, "rate_limit_rps": 100}},
        headers=auth(),
    )
    assert response.status_code == 200
    addons = response.json()["addons"]
    assert addons["geoip"] is True
    assert addons["rate_limit_rps"] == 100
    # Verify merge preserved untouched keys
    assert addons["bot_filter"] is False
    assert addons["gtm_js_proxy"] is True


@pytest.mark.asyncio
async def test_delete_client(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "D", "subdomain": "ddd", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    response = await api_client.delete(f"/api/v1/clients/{client_id}", headers=auth())
    assert response.status_code == 204
    get_resp = await api_client.get(f"/api/v1/clients/{client_id}", headers=auth())
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_suspend_client(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "E", "subdomain": "eee", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    response = await api_client.post(f"/api/v1/clients/{client_id}/suspend", headers=auth())
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_resume_client(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "F", "subdomain": "fff", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    await api_client.post(f"/api/v1/clients/{client_id}/suspend", headers=auth())
    response = await api_client.post(f"/api/v1/clients/{client_id}/resume", headers=auth())
    assert response.status_code == 200
    assert response.json()["status"] == "active"


@pytest.mark.asyncio
async def test_snippet_returns_gtm_url(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    r = await api_client.post("/api/v1/clients", json={"name": "G", "subdomain": "ggg", "container_config": "dGVzdA=="}, headers=auth())
    client_id = r.json()["id"]
    response = await api_client.get(f"/api/v1/clients/{client_id}/snippet", headers=auth())
    assert response.status_code == 200
    assert "ggg" in response.json()["server_url"]


@pytest.mark.asyncio
async def test_invalid_subdomain_rejected(api_client: AsyncClient, db: AsyncSession):
    await seed_key(db)
    response = await api_client.post(
        "/api/v1/clients",
        json={"name": "Bad", "subdomain": "../evil", "container_config": "dGVzdA=="},
        headers=auth(),
    )
    assert response.status_code == 422
