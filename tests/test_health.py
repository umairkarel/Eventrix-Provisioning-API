from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Client, ApiKey


@pytest.mark.asyncio
async def test_client_model_creates(db: AsyncSession):
    import uuid
    client = Client(
        id=uuid.uuid4(),
        name="Test Client",
        subdomain="test",
        container_config="dGVzdA==",
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    assert client.status.value == "provisioning"
    assert client.addons["rate_limit_rps"] == 50
