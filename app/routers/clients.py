import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import require_api_key
from app.database import get_session
from app.models import ApiKey, Client, ClientStatus
from app.schemas import AddonsSchema, ClientCreate, ClientResponse, ClientUpdate, parse_container_config
from app.services import traefik as traefik_svc
from app.services import docker_service, cloudflare
from app.config import settings

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


@router.post("", response_model=ClientResponse, status_code=201)
async def create_client(
    body: ClientCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    existing = await db.execute(
        select(Client).where(Client.subdomain == body.subdomain, Client.status != ClientStatus.deleted)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Subdomain already in use")

    addons_dict = body.addons.model_dump() if body.addons else AddonsSchema().model_dump()
    gtm_container_id, gtm_env = parse_container_config(body.container_config)
    client = Client(
        id=uuid.uuid4(),
        name=body.name,
        subdomain=body.subdomain,
        container_config=body.container_config,
        gtm_container_id=gtm_container_id,
        gtm_env=gtm_env,
        addons=addons_dict,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    try:
        dns_record_id = await cloudflare.create_a_record(body.subdomain)
        client.dns_record_id = dns_record_id
        await db.commit()

        traefik_svc.write_client_config(str(client.id), body.subdomain, addons_dict)
        if settings.traefik_restart_on_config_change:
            background_tasks.add_task(docker_service.restart_traefik)

        server_id, preview_id = await docker_service.start_gtm_containers(
            subdomain=body.subdomain,
            container_config=body.container_config,
        )
        client.server_container_id = server_id
        client.preview_container_id = preview_id
        await db.commit()
        await db.refresh(client)
    except Exception:
        logger.exception("Provisioning failed for subdomain %s", body.subdomain)
        # Best-effort cleanup of any resources created before the failure
        if client.dns_record_id:
            try:
                await cloudflare.delete_record(client.dns_record_id)
            except Exception:
                pass
        traefik_svc.delete_client_config(body.subdomain)
        client.status = ClientStatus.deleted
        await db.commit()
        raise HTTPException(status_code=502, detail="Provisioning failed; resources have been cleaned up")

    return client


@router.get("", response_model=list[ClientResponse])
async def list_clients(
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    result = await db.execute(
        select(Client).where(Client.status != ClientStatus.deleted)
    )
    return result.scalars().all()


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    if body.name is not None:
        client.name = body.name
    if body.container_config is not None:
        client.container_config = body.container_config
        client.gtm_container_id, client.gtm_env = parse_container_config(body.container_config)
    if body.addons is not None:
        client.addons = {**client.addons, **body.addons.model_dump(exclude_unset=True)}
        traefik_svc.write_client_config(str(client.id), client.subdomain, client.addons)
        if settings.traefik_restart_on_config_change:
            background_tasks.add_task(docker_service.restart_traefik)
    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")

    # Commit deleted status before stopping containers so the die event
    # from docker_events.py doesn't misclassify this as an unexpected failure.
    client.status = ClientStatus.deleted
    await db.commit()

    if client.server_container_id and client.preview_container_id:
        await docker_service.stop_containers(client.server_container_id, client.preview_container_id)

    if client.dns_record_id:
        await cloudflare.delete_record(client.dns_record_id)

    traefik_svc.delete_client_config(client.subdomain)
    if settings.traefik_restart_on_config_change:
        background_tasks.add_task(docker_service.restart_traefik)


@router.post("/{client_id}/suspend", response_model=ClientResponse)
async def suspend_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.status == ClientStatus.suspended:
        return client
    # Commit suspended status before stopping containers so the die event
    # from docker_events.py doesn't misclassify this as an unexpected failure.
    client.status = ClientStatus.suspended
    await db.commit()
    if client.server_container_id and client.preview_container_id:
        await docker_service.suspend_containers(client.server_container_id, client.preview_container_id)
    await db.refresh(client)
    return client


@router.post("/{client_id}/resume", response_model=ClientResponse)
async def resume_client(
    client_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")

    containers_running = False
    if client.server_container_id and client.preview_container_id:
        await docker_service.resume_containers(client.server_container_id, client.preview_container_id)
        health = await docker_service.container_health(client.server_container_id)
        containers_running = health != "not_found"

    if not containers_running:
        # Containers were removed — recreate them and wait for health events
        server_id, preview_id = await docker_service.start_gtm_containers(
            client.subdomain, client.container_config
        )
        client.server_container_id = server_id
        client.preview_container_id = preview_id
        client.status = ClientStatus.provisioning
    else:
        client.status = ClientStatus.active

    await db.commit()
    if settings.traefik_restart_on_config_change:
        background_tasks.add_task(docker_service.restart_traefik)
    await db.refresh(client)
    return client


@router.get("/{client_id}/health")
async def client_health(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    client = await db.get(Client, client_id)
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")
    container_status = "unknown"
    if client.server_container_id:
        container_status = await docker_service.container_health(client.server_container_id)
    return {
        "client_id": str(client_id),
        "status": client.status,
        "container_health": container_status,
        "subdomain": client.subdomain,
    }


@router.get("/{client_id}/snippet")
async def client_snippet(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    result = await db.execute(
        select(Client).where(Client.id == client_id).options(selectinload(Client.custom_domain))
    )
    client = result.scalar_one_or_none()
    if not client or client.status == ClientStatus.deleted:
        raise HTTPException(status_code=404, detail="Client not found")

    if client.custom_domain and client.custom_domain.status.value == "active":
        domain = client.custom_domain.domain
    else:
        domain = f"{client.subdomain}.{settings.base_domain}"

    server_url = f"https://{domain}"
    return {
        "server_url": server_url,
        "gtm_snippet": f'<script>window.dataLayer=window.dataLayer||[];(function(w,d,s,l,i){{...}})(window,document,"script","dataLayer","{server_url}");</script>',
    }
