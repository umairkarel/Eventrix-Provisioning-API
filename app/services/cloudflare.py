import asyncio
import socket
import httpx
from app.config import settings

_CF_API = "https://api.cloudflare.com/client/v4"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.cf_api_token}"}


async def create_a_record(subdomain: str) -> str:
    """Creates an A record for {subdomain}.{base_domain} → vm_ip. Returns the record ID."""
    if settings.mock_cloudflare:
        return f"local-{subdomain}"
    fqdn = f"{subdomain}.{settings.base_domain}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_CF_API}/zones/{settings.cf_zone_id}/dns_records",
            headers=_headers(),
            json={
                "type": "A",
                "name": fqdn,
                "content": settings.vm_ip,
                "ttl": 1,
                "proxied": False,
            },
        )
        resp.raise_for_status()
        return resp.json()["result"]["id"]


async def delete_record(record_id: str) -> None:
    if settings.mock_cloudflare:
        return
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{_CF_API}/zones/{settings.cf_zone_id}/dns_records/{record_id}",
            headers=_headers(),
        )
        if resp.status_code == 404:
            return
        resp.raise_for_status()


async def resolve_cname(domain: str) -> str | None:
    """Returns vm_ip if domain resolves to our IP, else None."""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: socket.getaddrinfo(domain, None))
        ips = {r[4][0] for r in result}
        return settings.vm_ip if settings.vm_ip in ips else None
    except OSError:
        return None
