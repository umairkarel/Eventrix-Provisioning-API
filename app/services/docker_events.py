import asyncio
import concurrent.futures
import logging
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Client, ClientStatus
from app.services.docker_service import _DockerClientCtx, container_health

logger = logging.getLogger(__name__)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="docker-events")


async def _recover_provisioning_clients() -> None:
    """On startup, immediately resolve any clients stuck in provisioning state."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(Client).where(Client.status == ClientStatus.provisioning)
        )
        clients = list(result.scalars().all())

    for client in clients:
        if not client.server_container_id:
            continue
        status = await container_health(client.server_container_id)
        if status == "healthy":
            async with SessionLocal() as db:
                c = await db.get(Client, client.id)
                if c and c.status == ClientStatus.provisioning:
                    c.status = ClientStatus.active
                    await db.commit()
                    logger.info("Recovery: client %s (%s) marked active", c.id, c.subdomain)
        else:
            logger.info(
                "Recovery: client %s (%s) still %s — will be caught by event stream",
                client.id, client.subdomain, status,
            )


async def _handle_event(event: dict) -> None:
    action = event.get("Action", "")
    container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "").lstrip("/")

    if not container_name.startswith("gtm-server-"):
        return

    subdomain = container_name.removeprefix("gtm-server-")

    if action.startswith("health_status:"):
        health_status = action.split(":", 1)[1].strip()
        logger.debug("Health event: %s → %s", container_name, health_status)

        if health_status == "healthy":
            async with SessionLocal() as db:
                result = await db.execute(
                    select(Client).where(
                        Client.subdomain == subdomain,
                        Client.status == ClientStatus.provisioning,
                    )
                )
                client = result.scalar_one_or_none()
                if client:
                    client.status = ClientStatus.active
                    await db.commit()
                    logger.info("Client %s (%s) is now active", client.id, subdomain)

        elif health_status == "unhealthy":
            logger.warning("Container %s reported unhealthy — monitoring for die event", container_name)

    elif action == "die":
        # Give the router a moment to commit an intentional suspend/delete before we check.
        # If the stop was intentional, the status will already be suspended/deleted by then.
        await asyncio.sleep(2)
        async with SessionLocal() as db:
            result = await db.execute(
                select(Client).where(
                    Client.subdomain == subdomain,
                    Client.status == ClientStatus.active,
                )
            )
            client = result.scalar_one_or_none()
            if client:
                client.status = ClientStatus.error
                await db.commit()
                logger.error(
                    "Container %s died unexpectedly — client %s (%s) marked error",
                    container_name, client.id, subdomain,
                )


async def _stream_events() -> None:
    """Blocks on the Docker event stream, dispatching events into the async world via a queue."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _run() -> None:
        try:
            with _DockerClientCtx() as dc:
                for event in dc.events(
                    filters={"event": ["health_status", "die"]}, decode=True
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    future = loop.run_in_executor(_executor, _run)
    try:
        while True:
            item = await queue.get()
            if isinstance(item, Exception):
                raise item
            await _handle_event(item)
    finally:
        future.cancel()


async def start_event_listener() -> None:
    """Persistent Docker event listener with exponential backoff reconnection."""
    backoff = 1
    while True:
        try:
            await _recover_provisioning_clients()
            logger.info("Docker event listener started")
            await _stream_events()
            backoff = 1
        except asyncio.CancelledError:
            logger.info("Docker event listener stopped")
            raise
        except Exception:
            logger.exception("Docker event listener crashed, reconnecting in %ds", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
