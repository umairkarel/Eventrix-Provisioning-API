import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import require_api_key
from app.database import get_session
from app.models import ApiKey, Client, ClientStatus, CustomDomain, CustomDomainStatus
from app.schemas import CustomDomainCreate, CustomDomainResponse
from app.services.dns_poller import poll_dns_until_verified
from app.config import settings

router = APIRouter(prefix="/api/v1/clients/{client_id}/custom-domain", tags=["custom-domain"])


async def _get_active_client(client_id: uuid.UUID, db: AsyncSession) -> Client:
    result = await db.execute(
        select(Client).where(Client.id == client_id).options(selectinload(Client.custom_domain))
    )
    client = result.scalar_one_or_none()
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.post("", response_model=CustomDomainResponse, status_code=201)
async def add_custom_domain(
    client_id: uuid.UUID,
    body: CustomDomainCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await _get_active_client(client_id, db)
    if client.custom_domain:
        raise HTTPException(status_code=409, detail="Client already has a custom domain")

    existing = await db.execute(select(CustomDomain).where(CustomDomain.domain == body.domain))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Domain already registered")

    cd = CustomDomain(
        id=uuid.uuid4(),
        client_id=client_id,
        domain=body.domain,
    )
    db.add(cd)
    await db.commit()
    await db.refresh(cd)

    background_tasks.add_task(poll_dns_until_verified, cd.id)

    cname_target = f"{client.subdomain}.{settings.base_domain}"
    return CustomDomainResponse(
        id=cd.id,
        client_id=cd.client_id,
        domain=cd.domain,
        status=cd.status,
        verified_at=cd.verified_at,
        created_at=cd.created_at,
        cname_target=cname_target,
    )


@router.get("/status", response_model=CustomDomainResponse)
async def custom_domain_status(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await _get_active_client(client_id, db)
    if not client.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    cd = client.custom_domain
    cname_target = f"{client.subdomain}.{settings.base_domain}"
    return CustomDomainResponse(
        id=cd.id,
        client_id=cd.client_id,
        domain=cd.domain,
        status=cd.status,
        verified_at=cd.verified_at,
        created_at=cd.created_at,
        cname_target=cname_target,
    )


@router.post("/verify")
async def trigger_verify(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await _get_active_client(client_id, db)
    if not client.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    cd = client.custom_domain
    if cd.status not in (CustomDomainStatus.pending_dns, CustomDomainStatus.failed):
        return {"detail": f"Nothing to verify, current status: {cd.status}"}
    cd.status = CustomDomainStatus.pending_dns
    await db.commit()
    background_tasks.add_task(poll_dns_until_verified, cd.id)
    return {"detail": "DNS verification restarted"}


@router.delete("", status_code=204)
async def remove_custom_domain(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await _get_active_client(client_id, db)
    if not client.custom_domain:
        raise HTTPException(status_code=404, detail="No custom domain registered")
    await db.delete(client.custom_domain)
    await db.commit()
    db.expire_all()
