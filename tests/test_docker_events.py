"""Tests for event-driven Docker container status transitions."""
import uuid
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.database import Base
from app.models import Server, ServerStatus, Tenant
from app.services.docker_events import _handle_event, _recover_provisioning_servers

# Dedicated in-memory engine with StaticPool so all sessions in a test share one connection.
_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

_TENANT_ID = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


@pytest.fixture(autouse=True)
async def reset_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with _Session() as s:
        s.add(Tenant(id=_TENANT_ID, name="Test Tenant"))
        await s.commit()
    yield


@pytest.fixture
async def db():
    async with _Session() as session:
        yield session


async def _insert(db: AsyncSession, *, subdomain: str, status: ServerStatus, server_id: str | None = None) -> Server:
    s = Server(
        id=uuid.uuid4(),
        tenant_id=_TENANT_ID,
        name="Test",
        subdomain=subdomain,
        container_config="dGVzdA==",
        status=status,
        server_container_id=server_id,
    )
    db.add(s)
    await db.commit()
    return s


async def _fetch(subdomain: str) -> Server:
    async with _Session() as s:
        return (await s.execute(select(Server).where(Server.subdomain == subdomain))).scalar_one()


def _health_event(subdomain: str, health: str) -> dict:
    return {
        "Action": f"health_status: {health}",
        "Actor": {"Attributes": {"name": f"gtm-server-{subdomain}"}},
    }


def _die_event(subdomain: str) -> dict:
    return {
        "Action": "die",
        "Actor": {"Attributes": {"name": f"gtm-server-{subdomain}"}},
    }


async def test_healthy_event_activates_provisioning_server(db):
    await _insert(db, subdomain="alpha", status=ServerStatus.provisioning)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("alpha", "healthy"))

    assert (await _fetch("alpha")).status == ServerStatus.active


async def test_healthy_event_ignores_already_active_server(db):
    await _insert(db, subdomain="beta", status=ServerStatus.active)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("beta", "healthy"))

    assert (await _fetch("beta")).status == ServerStatus.active


async def test_unhealthy_event_leaves_status_unchanged(db):
    await _insert(db, subdomain="gamma", status=ServerStatus.active)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("gamma", "unhealthy"))

    assert (await _fetch("gamma")).status == ServerStatus.active


async def test_die_event_marks_active_server_as_error(db):
    await _insert(db, subdomain="delta", status=ServerStatus.active)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("delta"))

    assert (await _fetch("delta")).status == ServerStatus.error


async def test_die_event_ignores_suspended_server(db):
    """Intentional suspend commits status before stopping containers; die must not overwrite it."""
    await _insert(db, subdomain="epsilon", status=ServerStatus.suspended)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("epsilon"))

    assert (await _fetch("epsilon")).status == ServerStatus.suspended


async def test_die_event_ignores_deleted_server(db):
    """Intentional delete commits status before stopping containers; die must not overwrite it."""
    await _insert(db, subdomain="zeta", status=ServerStatus.deleted)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("zeta"))

    assert (await _fetch("zeta")).status == ServerStatus.deleted


async def test_event_for_non_gtm_container_is_ignored(db):
    await _insert(db, subdomain="eta", status=ServerStatus.active)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event({
            "Action": "die",
            "Actor": {"Attributes": {"name": "nginx-proxy"}},
        })

    assert (await _fetch("eta")).status == ServerStatus.active


async def test_recover_marks_healthy_provisioning_server_active(db):
    await _insert(db, subdomain="theta", status=ServerStatus.provisioning, server_id="srv-1")

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="healthy"),
    ):
        await _recover_provisioning_servers()

    assert (await _fetch("theta")).status == ServerStatus.active


async def test_recover_leaves_unhealthy_server_in_provisioning(db):
    await _insert(db, subdomain="iota", status=ServerStatus.provisioning, server_id="srv-2")

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="starting"),
    ):
        await _recover_provisioning_servers()

    assert (await _fetch("iota")).status == ServerStatus.provisioning


async def test_recover_skips_server_without_container(db):
    """A provisioning server with no server_container_id has nothing to check."""
    await _insert(db, subdomain="kappa", status=ServerStatus.provisioning, server_id=None)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="healthy"),
    ):
        await _recover_provisioning_servers()

    assert (await _fetch("kappa")).status == ServerStatus.provisioning
