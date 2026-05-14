import secrets
import uuid
import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_session
from app.models import ApiKey, Server, ServerStatus, Tenant
from app.schemas import ApiKeyCreate, ApiKeyCreateResponse, ApiKeyResponse, TenantCreate, TenantResponse

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.post("", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_session),
):
    tenant = Tenant(id=uuid.uuid4(), name=body.name)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("", response_model=list[TenantResponse])
async def list_tenants(db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Tenant))
    return result.scalars().all()


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    active = await db.execute(
        select(Server).where(
            Server.tenant_id == tenant_id,
            Server.status != ServerStatus.deleted,
        )
    )
    if active.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Cannot delete tenant with active servers")
    await db.delete(tenant)
    await db.commit()


@router.post("/{tenant_id}/keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    tenant_id: uuid.UUID,
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_session),
):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    raw_key = secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        key_hash=key_hash,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return ApiKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        created_at=api_key.created_at,
        key=raw_key,
    )


@router.get("/{tenant_id}/keys", response_model=list[ApiKeyResponse])
async def list_api_keys(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    result = await db.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
    return result.scalars().all()


@router.delete("/{tenant_id}/keys/{key_id}", status_code=204)
async def revoke_api_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
):
    key = await db.get(ApiKey, key_id)
    if not key or key.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="API key not found")
    await db.delete(key)
    await db.commit()
