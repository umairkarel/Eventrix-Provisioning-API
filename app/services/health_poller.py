import asyncio
import uuid
from app.database import SessionLocal
from app.models import Client, ClientStatus
from app.services.docker_service import container_health


async def poll_until_healthy(client_id: uuid.UUID, max_attempts: int = 18) -> None:
    """Polls container health every 10s for up to 3 minutes, then marks client active."""
    for _ in range(max_attempts):
        await asyncio.sleep(10)
        async with SessionLocal() as db:
            client = await db.get(Client, client_id)
            if not client or client.status != ClientStatus.provisioning:
                return
            if not client.server_container_id:
                continue
            status = await container_health(client.server_container_id)
            if status == "healthy":
                client.status = ClientStatus.active
                await db.commit()
                return
    async with SessionLocal() as db:
        client = await db.get(Client, client_id)
        if client and client.status == ClientStatus.provisioning:
            client.status = ClientStatus.suspended
            await db.commit()
