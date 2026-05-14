import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import select
from app.routers import servers, domains, tenants
from app.services.docker_events import start_event_listener
from app.services import traefik as traefik_svc
from app.services import docker_service
from app.database import SessionLocal
from app.models import Server, ServerStatus
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


async def sync_sidecars() -> None:
    """Start any stopped sidecar (NGINX HTTPS proxy) containers for active servers on startup."""
    if not settings.preview_use_sidecar:
        return
    async with SessionLocal() as session:
        result = await session.execute(
            select(Server).where(Server.status == ServerStatus.active)
        )
        active_servers = result.scalars().all()
    recovered = 0
    for server in active_servers:
        try:
            await docker_service.ensure_sidecar_running(server.subdomain)
            recovered += 1
        except Exception:
            logger.exception("Failed to ensure sidecar for server %s", server.subdomain)
    logger.info("Sidecar sync complete: checked %d server(s)", recovered)


async def sync_traefik_configs() -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Server).where(Server.status != ServerStatus.deleted)
        )
        active_servers = result.scalars().all()

    updated = 0
    for server in active_servers:
        try:
            traefik_svc.write_client_config(str(server.id), server.subdomain, server.addons)
            updated += 1
        except Exception:
            logger.exception("Failed to sync Traefik config for server %s", server.subdomain)

    if updated:
        logger.info("Synced Traefik configs for %d server(s)", updated)
        if settings.traefik_restart_on_config_change:
            await docker_service.restart_traefik()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await sync_traefik_configs()
    await sync_sidecars()
    task = asyncio.create_task(start_event_listener())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="GTM Provisioning API", version="1.0.0", lifespan=lifespan)
app.include_router(tenants.router)
app.include_router(servers.router)
app.include_router(domains.router)


@app.get("/healthz")
def health():
    return {"status": "ok"}
