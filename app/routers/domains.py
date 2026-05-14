import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import require_api_key
from app.database import get_session
from app.models import ApiKey, Server, ServerStatus, CustomDomain, CustomDomainStatus
from app.schemas import CustomDomainCreate, CustomDomainResponse
from app.services.dns_poller import poll_dns_until_verified
from app.config import settings

router = APIRouter(prefix="/api/v1/servers/{server_id}/custom-domain", tags=["custom-domain"])


async def _get_active_server(server_id: uuid.UUID, tenant_id: uuid.UUID, db: AsyncSession) -> Server:
    result = await db.execute(
        select(Server).where(Server.id == server_id).options(selectinload(Server.custom_domain))
    )
    server = result.scalar_one_or_none()
    if not server or server.status == ServerStatus.deleted or server.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.post("", response_model=CustomDomainResponse, status_code=201)
async def add_custom_domain(
    server_id: uuid.UUID,
    body: CustomDomainCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await _get_active_server(server_id, _key.tenant_id, db)
    if server.custom_domain:
        raise HTTPException(status_code=409, detail="Server already has a custom domain")

    existing = await db.execute(select(CustomDomain).where(CustomDomain.domain == body.domain))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Domain already registered")

    cd = CustomDomain(id=uuid.uuid4(), server_id=server_id, domain=body.domain)
    db.add(cd)
    await db.commit()
    await db.refresh(cd)

    background_tasks.add_task(poll_dns_until_verified, cd.id)

    cname_target = f"{server.subdomain}.{settings.base_domain}"
    return CustomDomainResponse(
        id=cd.id,
        server_id=cd.server_id,
        domain=cd.domain,
        status=cd.status,
        verified_at=cd.verified_at,
        created_at=cd.created_at,
        cname_target=cname_target,
    )


@router.get("/status", response_model=CustomDomainResponse)
async def custom_domain_status(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await _get_active_server(server_id, _key.tenant_id, db)
    if not server.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    cd = server.custom_domain
    return CustomDomainResponse(
        id=cd.id,
        server_id=cd.server_id,
        domain=cd.domain,
        status=cd.status,
        verified_at=cd.verified_at,
        created_at=cd.created_at,
        cname_target=f"{server.subdomain}.{settings.base_domain}",
    )


@router.post("/verify")
async def trigger_verify(
    server_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await _get_active_server(server_id, _key.tenant_id, db)
    if not server.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    cd = server.custom_domain
    if cd.status not in (CustomDomainStatus.pending_dns, CustomDomainStatus.failed):
        return {"detail": f"Nothing to verify, current status: {cd.status}"}
    cd.status = CustomDomainStatus.pending_dns
    await db.commit()
    background_tasks.add_task(poll_dns_until_verified, cd.id)
    return {"detail": "DNS verification restarted"}


@router.delete("", status_code=204)
async def remove_custom_domain(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await _get_active_server(server_id, _key.tenant_id, db)
    if not server.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    await db.delete(server.custom_domain)
    await db.commit()
    db.expire_all()
