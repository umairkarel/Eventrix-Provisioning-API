import asyncio
import uuid
from app.database import SessionLocal
from app.models import CustomDomain, CustomDomainStatus
from app.services.cloudflare import resolve_cname


async def poll_dns_until_verified(custom_domain_id: uuid.UUID, max_attempts: int = 60) -> None:
    """Polls DNS every 30s for up to 30 minutes. On success, transitions to pending_cert."""
    for _ in range(max_attempts):
        await asyncio.sleep(30)
        async with SessionLocal() as db:
            cd = await db.get(CustomDomain, custom_domain_id)
            if not cd or cd.status != CustomDomainStatus.pending_dns:
                return
            result = await resolve_cname(cd.domain)
            if result:
                cd.status = CustomDomainStatus.pending_cert
                await db.commit()
                return
    async with SessionLocal() as db:
        cd = await db.get(CustomDomain, custom_domain_id)
        if cd and cd.status == CustomDomainStatus.pending_dns:
            cd.status = CustomDomainStatus.failed
            await db.commit()
