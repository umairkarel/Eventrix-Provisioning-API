import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def make_tenant_with_key(api_client: AsyncClient) -> tuple[str, str]:
    """Creates a tenant and returns (tenant_id, raw_api_key)."""
    r = await api_client.post("/api/v1/tenants", json={"name": "Test Tenant"})
    tenant_id = r.json()["id"]
    kr = await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k"})
    return tenant_id, kr.json()["key"]


def auth(key: str) -> dict:
    return {"X-API-Key": key}


@pytest.mark.asyncio
async def test_create_server(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    response = await api_client.post(
        "/api/v1/servers",
        json={"name": "Acme GTM", "subdomain": "acme", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["subdomain"] == "acme"
    assert data["status"] == "provisioning"
    assert data["addons"]["rate_limit_rps"] == 50
    assert "tenant_id" in data


@pytest.mark.asyncio
async def test_create_server_duplicate_subdomain(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    payload = {"name": "A", "subdomain": "dup", "container_config": "dGVzdA=="}
    await api_client.post("/api/v1/servers", json=payload, headers=auth(key))
    r = await api_client.post("/api/v1/servers", json=payload, headers=auth(key))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_servers_scoped_to_tenant(api_client: AsyncClient):
    _, key_a = await make_tenant_with_key(api_client)
    _, key_b = await make_tenant_with_key(api_client)

    await api_client.post(
        "/api/v1/servers",
        json={"name": "A1", "subdomain": "a1", "container_config": "dGVzdA=="},
        headers=auth(key_a),
    )
    await api_client.post(
        "/api/v1/servers",
        json={"name": "B1", "subdomain": "b1", "container_config": "dGVzdA=="},
        headers=auth(key_b),
    )

    r_a = await api_client.get("/api/v1/servers", headers=auth(key_a))
    r_b = await api_client.get("/api/v1/servers", headers=auth(key_b))

    assert len(r_a.json()) == 1
    assert r_a.json()[0]["subdomain"] == "a1"
    assert len(r_b.json()) == 1
    assert r_b.json()[0]["subdomain"] == "b1"


@pytest.mark.asyncio
async def test_get_server_cross_tenant_returns_404(api_client: AsyncClient):
    _, key_a = await make_tenant_with_key(api_client)
    _, key_b = await make_tenant_with_key(api_client)

    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "A", "subdomain": "aaa", "container_config": "dGVzdA=="},
        headers=auth(key_a),
    )
    server_id = r.json()["id"]

    response = await api_client.get(f"/api/v1/servers/{server_id}", headers=auth(key_b))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_server(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "B", "subdomain": "bbb", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    server_id = r.json()["id"]
    response = await api_client.get(f"/api/v1/servers/{server_id}", headers=auth(key))
    assert response.status_code == 200
    assert response.json()["id"] == server_id


@pytest.mark.asyncio
async def test_patch_server_addons(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "C", "subdomain": "ccc", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    server_id = r.json()["id"]
    response = await api_client.patch(
        f"/api/v1/servers/{server_id}",
        json={"addons": {"geoip": True, "rate_limit_rps": 100}},
        headers=auth(key),
    )
    assert response.status_code == 200
    assert response.json()["addons"]["geoip"] is True
    assert response.json()["addons"]["rate_limit_rps"] == 100
    assert response.json()["addons"]["bot_filter"] is False


@pytest.mark.asyncio
async def test_delete_server(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "D", "subdomain": "ddd", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    server_id = r.json()["id"]
    assert (await api_client.delete(f"/api/v1/servers/{server_id}", headers=auth(key))).status_code == 204
    assert (await api_client.get(f"/api/v1/servers/{server_id}", headers=auth(key))).status_code == 404


@pytest.mark.asyncio
async def test_suspend_and_resume_server(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "E", "subdomain": "eee", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    server_id = r.json()["id"]

    suspend_r = await api_client.post(f"/api/v1/servers/{server_id}/suspend", headers=auth(key))
    assert suspend_r.status_code == 200
    assert suspend_r.json()["status"] == "suspended"

    resume_r = await api_client.post(f"/api/v1/servers/{server_id}/resume", headers=auth(key))
    assert resume_r.status_code == 200


@pytest.mark.asyncio
async def test_snippet_contains_subdomain(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "F", "subdomain": "fff", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    server_id = r.json()["id"]
    response = await api_client.get(f"/api/v1/servers/{server_id}/snippet", headers=auth(key))
    assert response.status_code == 200
    assert "fff" in response.json()["server_url"]


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(api_client: AsyncClient):
    response = await api_client.get("/api/v1/servers")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_subdomain_rejected(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    response = await api_client.post(
        "/api/v1/servers",
        json={"name": "Bad", "subdomain": "../evil", "container_config": "dGVzdA=="},
        headers=auth(key),
    )
    assert response.status_code == 422
