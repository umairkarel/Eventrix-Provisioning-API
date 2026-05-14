import asyncio
import concurrent.futures
import logging
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Server, ServerStatus
from app.services.docker_service import _DockerClientCtx, container_health

logger = logging.getLogger(__name__)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="docker-events")


_TERMINAL_CONTAINER_STATES = frozenset({"not_found", "exited", "dead"})


async def _recover_provisioning_servers() -> None:
    async with SessionLocal() as db:
        result = await db.execute(
            select(Server).where(Server.status == ServerStatus.provisioning)
        )
        servers = list(result.scalars().all())

    for server in servers:
        if not server.server_container_id:
            continue
        status = await container_health(server.server_container_id)
        if status == "healthy":
            async with SessionLocal() as db:
                s = await db.get(Server, server.id)
                if s and s.status == ServerStatus.provisioning:
                    s.status = ServerStatus.active
                    await db.commit()
                    logger.info("Recovery: server %s (%s) marked active", s.id, s.subdomain)
        elif status in _TERMINAL_CONTAINER_STATES:
            # Container is gone — the die event fired before we restarted so it
            # will never arrive on the stream. Mark error so the server isn't
            # stuck in provisioning indefinitely.
            async with SessionLocal() as db:
                s = await db.get(Server, server.id)
                if s and s.status == ServerStatus.provisioning:
                    s.status = ServerStatus.error
                    await db.commit()
                    logger.error(
                        "Recovery: server %s (%s) container is %s — marked error",
                        s.id, s.subdomain, status,
                    )
        else:
            logger.info(
                "Recovery: server %s (%s) is %s — waiting for health event",
                server.id, server.subdomain, status,
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
                    select(Server).where(
                        Server.subdomain == subdomain,
                        Server.status == ServerStatus.provisioning,
                    )
                )
                server = result.scalar_one_or_none()
                if server:
                    server.status = ServerStatus.active
                    await db.commit()
                    logger.info("Server %s (%s) is now active", server.id, subdomain)

        elif health_status == "unhealthy":
            logger.warning("Container %s reported unhealthy", container_name)

    elif action == "die":
        # Brief delay to let Docker settle before reading state (avoids racing a
        # restart that would make the container appear running again immediately).
        await asyncio.sleep(2)
        async with SessionLocal() as db:
            result = await db.execute(
                select(Server).where(
                    Server.subdomain == subdomain,
                    Server.status.in_([ServerStatus.active, ServerStatus.provisioning]),
                )
            )
            server = result.scalar_one_or_none()
            if server:
                server.status = ServerStatus.error
                await db.commit()
                logger.error(
                    "Container %s died — server %s (%s) marked error",
                    container_name, server.id, subdomain,
                )


async def _stream_events() -> None:
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
    backoff = 1
    while True:
        try:
            await _recover_provisioning_servers()
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
