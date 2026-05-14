import uuid
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.models import Server, Tenant


def test_health():
    with (
        patch("app.main.sync_traefik_configs", new_callable=AsyncMock),
        patch("app.main.sync_sidecars", new_callable=AsyncMock),
        patch("app.main.start_event_listener", new_callable=AsyncMock),
    ):
        with TestClient(app) as client:
            response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_server_model_creates(db: AsyncSession):
    tenant = Tenant(id=uuid.uuid4(), name="Test Tenant")
    db.add(tenant)
    await db.flush()

    server = Server(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Test Server",
        subdomain="test",
        container_config="dGVzdA==",
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    assert server.status.value == "provisioning"
    assert server.addons["rate_limit_rps"] == 50
