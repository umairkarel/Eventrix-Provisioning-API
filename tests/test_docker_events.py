"""Tests for event-driven Docker container status transitions."""
import uuid
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.database import Base
from app.models import Client, ClientStatus
from app.services.docker_events import _handle_event, _recover_provisioning_clients

# Dedicated in-memory engine with StaticPool so all sessions in a test share one connection.
_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def reset_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def db():
    async with _Session() as session:
        yield session


async def _insert(db: AsyncSession, *, subdomain: str, status: ClientStatus, server_id: str | None = None) -> Client:
    c = Client(
        id=uuid.uuid4(),
        name="Test",
        subdomain=subdomain,
        container_config="dGVzdA==",
        status=status,
        server_container_id=server_id,
    )
    db.add(c)
    await db.commit()
    return c


async def _fetch(subdomain: str) -> Client:
    async with _Session() as s:
        return (await s.execute(select(Client).where(Client.subdomain == subdomain))).scalar_one()


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


async def test_healthy_event_activates_provisioning_client(db):
    await _insert(db, subdomain="alpha", status=ClientStatus.provisioning)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("alpha", "healthy"))

    assert (await _fetch("alpha")).status == ClientStatus.active


async def test_healthy_event_ignores_already_active_client(db):
    await _insert(db, subdomain="beta", status=ClientStatus.active)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("beta", "healthy"))

    assert (await _fetch("beta")).status == ClientStatus.active


async def test_unhealthy_event_leaves_status_unchanged(db):
    await _insert(db, subdomain="gamma", status=ClientStatus.active)

    with patch("app.services.docker_events.SessionLocal", _Session):
        await _handle_event(_health_event("gamma", "unhealthy"))

    assert (await _fetch("gamma")).status == ClientStatus.active


async def test_die_event_marks_active_client_as_error(db):
    await _insert(db, subdomain="delta", status=ClientStatus.active)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("delta"))

    assert (await _fetch("delta")).status == ClientStatus.error


async def test_die_event_ignores_suspended_client(db):
    """Intentional suspend commits status before stopping containers; die must not overwrite it."""
    await _insert(db, subdomain="epsilon", status=ClientStatus.suspended)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("epsilon"))

    assert (await _fetch("epsilon")).status == ClientStatus.suspended


async def test_die_event_ignores_deleted_client(db):
    """Intentional delete commits status before stopping containers; die must not overwrite it."""
    await _insert(db, subdomain="zeta", status=ClientStatus.deleted)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event(_die_event("zeta"))

    assert (await _fetch("zeta")).status == ClientStatus.deleted


async def test_event_for_non_gtm_container_is_ignored(db):
    await _insert(db, subdomain="eta", status=ClientStatus.active)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await _handle_event({
            "Action": "die",
            "Actor": {"Attributes": {"name": "nginx-proxy"}},
        })

    assert (await _fetch("eta")).status == ClientStatus.active


async def test_recover_marks_healthy_provisioning_client_active(db):
    await _insert(db, subdomain="theta", status=ClientStatus.provisioning, server_id="srv-1")

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="healthy"),
    ):
        await _recover_provisioning_clients()

    assert (await _fetch("theta")).status == ClientStatus.active


async def test_recover_leaves_unhealthy_client_in_provisioning(db):
    await _insert(db, subdomain="iota", status=ClientStatus.provisioning, server_id="srv-2")

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="starting"),
    ):
        await _recover_provisioning_clients()

    assert (await _fetch("iota")).status == ClientStatus.provisioning


async def test_recover_skips_client_without_container(db):
    """A provisioning client with no server_container_id has nothing to check."""
    await _insert(db, subdomain="kappa", status=ClientStatus.provisioning, server_id=None)

    with (
        patch("app.services.docker_events.SessionLocal", _Session),
        patch("app.services.docker_events.container_health", new_callable=AsyncMock, return_value="healthy"),
    ):
        await _recover_provisioning_clients()

    assert (await _fetch("kappa")).status == ClientStatus.provisioning
