import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import app
from app.database import get_session, Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock all external service calls (Cloudflare, Docker, Traefik file I/O) in every test."""
    with (
        patch("app.routers.servers.cloudflare.create_a_record", new_callable=AsyncMock, return_value="dns-record-id"),
        patch("app.routers.servers.cloudflare.delete_record", new_callable=AsyncMock),
        patch("app.routers.servers.docker_service.start_gtm_containers", new_callable=AsyncMock, return_value=("server-cid", "preview-cid")),
        patch("app.routers.servers.docker_service.stop_containers", new_callable=AsyncMock),
        patch("app.routers.servers.docker_service.suspend_containers", new_callable=AsyncMock),
        patch("app.routers.servers.docker_service.resume_containers", new_callable=AsyncMock),
        patch("app.routers.servers.docker_service.container_health", new_callable=AsyncMock, return_value="healthy"),
        patch("app.routers.servers.traefik_svc.write_client_config"),
        patch("app.routers.servers.traefik_svc.delete_client_config"),
    ):
        yield


@pytest_asyncio.fixture
async def api_client(db: AsyncSession):
    async def override_session():
        yield db

    app.dependency_overrides[get_session] = override_session
    with patch("app.main.start_event_listener", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    app.dependency_overrides.clear()
