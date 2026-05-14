import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Tenant


@pytest.mark.asyncio
async def test_create_tenant(api_client: AsyncClient):
    response = await api_client.post("/api/v1/tenants", json={"name": "Acme Corp"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Acme Corp"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_list_tenants(api_client: AsyncClient):
    await api_client.post("/api/v1/tenants", json={"name": "Tenant A"})
    await api_client.post("/api/v1/tenants", json={"name": "Tenant B"})
    response = await api_client.get("/api/v1/tenants")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_tenant(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "Acme"})
    tenant_id = r.json()["id"]
    response = await api_client.get(f"/api/v1/tenants/{tenant_id}")
    assert response.status_code == 200
    assert response.json()["id"] == tenant_id


@pytest.mark.asyncio
async def test_get_tenant_not_found(api_client: AsyncClient):
    response = await api_client.get(f"/api/v1/tenants/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_api_key_returns_raw_key_once(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "KeyTest"})
    tenant_id = r.json()["id"]

    response = await api_client.post(
        f"/api/v1/tenants/{tenant_id}/keys", json={"name": "my-key"}
    )
    assert response.status_code == 201
    data = response.json()
    assert "key" in data
    assert len(data["key"]) > 20
    assert "id" in data
    assert data["name"] == "my-key"


@pytest.mark.asyncio
async def test_list_api_keys_does_not_expose_raw_key(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "T"})
    tenant_id = r.json()["id"]
    await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k1"})
    await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k2"})

    response = await api_client.get(f"/api/v1/tenants/{tenant_id}/keys")
    assert response.status_code == 200
    keys = response.json()
    assert len(keys) == 2
    for k in keys:
        assert "key" not in k
        assert "key_hash" not in k


@pytest.mark.asyncio
async def test_revoke_api_key(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "T"})
    tenant_id = r.json()["id"]
    kr = await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k"})
    key_id = kr.json()["id"]

    response = await api_client.delete(f"/api/v1/tenants/{tenant_id}/keys/{key_id}")
    assert response.status_code == 204

    list_r = await api_client.get(f"/api/v1/tenants/{tenant_id}/keys")
    assert len(list_r.json()) == 0


@pytest.mark.asyncio
async def test_delete_tenant_with_no_servers(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "ToDelete"})
    tenant_id = r.json()["id"]
    response = await api_client.delete(f"/api/v1/tenants/{tenant_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_created_key_authenticates_server_route(api_client: AsyncClient):
    r = await api_client.post("/api/v1/tenants", json={"name": "AuthTest"})
    tenant_id = r.json()["id"]
    kr = await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k"})
    raw_key = kr.json()["key"]

    response = await api_client.get("/api/v1/servers", headers={"X-API-Key": raw_key})
    assert response.status_code == 200
    assert response.json() == []
