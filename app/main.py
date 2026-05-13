import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import select
from app.routers import clients, domains
from app.services.docker_events import start_event_listener
from app.services import traefik as traefik_svc
from app.services import docker_service
from app.database import SessionLocal
from app.models import Client, ClientStatus
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


async def sync_traefik_configs() -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Client).where(Client.status != ClientStatus.deleted)
        )
        active_clients = result.scalars().all()

    updated = 0
    for client in active_clients:
        try:
            traefik_svc.write_client_config(str(client.id), client.subdomain, client.addons)
            updated += 1
        except Exception:
            logger.exception("Failed to sync Traefik config for client %s", client.subdomain)

    if updated:
        logger.info("Synced Traefik configs for %d client(s)", updated)
        if settings.traefik_restart_on_config_change:
            await docker_service.restart_traefik()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await sync_traefik_configs()
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
app.include_router(clients.router)
app.include_router(domains.router)


@app.get("/healthz")
def health():
    return {"status": "ok"}
