import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient


async def make_tenant_with_key(api_client: AsyncClient) -> tuple[str, str]:
    r = await api_client.post("/api/v1/tenants", json={"name": "Domain Test Tenant"})
    tenant_id = r.json()["id"]
    kr = await api_client.post(f"/api/v1/tenants/{tenant_id}/keys", json={"name": "k"})
    return tenant_id, kr.json()["key"]


async def create_server(api_client: AsyncClient, subdomain: str, key: str) -> str:
    r = await api_client.post(
        "/api/v1/servers",
        json={"name": "Test", "subdomain": subdomain, "container_config": "dGVzdA=="},
        headers={"X-API-Key": key},
    )
    return r.json()["id"]


@pytest.mark.asyncio
async def test_add_custom_domain(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    server_id = await create_server(api_client, "cust1", key)
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        response = await api_client.post(
            f"/api/v1/servers/{server_id}/custom-domain",
            json={"domain": "tracking.example.com"},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["domain"] == "tracking.example.com"
    assert data["status"] == "pending_dns"
    assert data["cname_target"] is not None


@pytest.mark.asyncio
async def test_add_custom_domain_duplicate(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    server_id = await create_server(api_client, "cust2", key)
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(
            f"/api/v1/servers/{server_id}/custom-domain",
            json={"domain": "dup.example.com"},
            headers={"X-API-Key": key},
        )
        response = await api_client.post(
            f"/api/v1/servers/{server_id}/custom-domain",
            json={"domain": "dup2.example.com"},
            headers={"X-API-Key": key},
        )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_custom_domain_status(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    server_id = await create_server(api_client, "cust3", key)
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(
            f"/api/v1/servers/{server_id}/custom-domain",
            json={"domain": "status.example.com"},
            headers={"X-API-Key": key},
        )
    response = await api_client.get(
        f"/api/v1/servers/{server_id}/custom-domain/status",
        headers={"X-API-Key": key},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending_dns"


@pytest.mark.asyncio
async def test_remove_custom_domain(api_client: AsyncClient):
    _, key = await make_tenant_with_key(api_client)
    server_id = await create_server(api_client, "cust4", key)
    with patch("app.routers.domains.poll_dns_until_verified", new_callable=AsyncMock):
        await api_client.post(
            f"/api/v1/servers/{server_id}/custom-domain",
            json={"domain": "remove.example.com"},
            headers={"X-API-Key": key},
        )
    response = await api_client.delete(
        f"/api/v1/servers/{server_id}/custom-domain",
        headers={"X-API-Key": key},
    )
    assert response.status_code == 204
    status_resp = await api_client.get(
        f"/api/v1/servers/{server_id}/custom-domain/status",
        headers={"X-API-Key": key},
    )
    assert status_resp.status_code == 404
