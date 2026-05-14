import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import require_api_key
from app.database import get_session
from app.models import ApiKey, Server, ServerStatus
from app.schemas import AddonsSchema, ServerCreate, ServerResponse, ServerUpdate, parse_container_config
from app.services import traefik as traefik_svc
from app.services import docker_service, cloudflare
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/servers", tags=["servers"])


@router.post("", response_model=ServerResponse, status_code=201)
async def create_server(
    body: ServerCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    existing = await db.execute(
        select(Server).where(
            Server.subdomain == body.subdomain,
            Server.status != ServerStatus.deleted,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Subdomain already in use")

    addons_dict = body.addons.model_dump() if body.addons else AddonsSchema().model_dump()
    gtm_container_id, gtm_env = parse_container_config(body.container_config)
    server = Server(
        id=uuid.uuid4(),
        tenant_id=_key.tenant_id,
        name=body.name,
        subdomain=body.subdomain,
        container_config=body.container_config,
        gtm_container_id=gtm_container_id,
        gtm_env=gtm_env,
        addons=addons_dict,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    try:
        dns_record_id = await cloudflare.create_a_record(body.subdomain)
        server.dns_record_id = dns_record_id
        await db.commit()

        traefik_svc.write_client_config(str(server.id), body.subdomain, addons_dict)
        if settings.traefik_restart_on_config_change:
            background_tasks.add_task(docker_service.restart_traefik)

        server_id, preview_id = await docker_service.start_gtm_containers(
            subdomain=body.subdomain,
            container_config=body.container_config,
        )
        server.server_container_id = server_id
        server.preview_container_id = preview_id
        await db.commit()
        await db.refresh(server)
    except Exception:
        logger.exception("Provisioning failed for subdomain %s", body.subdomain)
        if server.dns_record_id:
            try:
                await cloudflare.delete_record(server.dns_record_id)
            except Exception:
                pass
        traefik_svc.delete_client_config(body.subdomain)
        server.status = ServerStatus.deleted
        await db.commit()
        raise HTTPException(status_code=502, detail="Provisioning failed; resources have been cleaned up")

    return server


@router.get("", response_model=list[ServerResponse])
async def list_servers(
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    result = await db.execute(
        select(Server).where(
            Server.tenant_id == _key.tenant_id,
            Server.status != ServerStatus.deleted,
        )
    )
    return result.scalars().all()


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.patch("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: uuid.UUID,
    body: ServerUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")
    if body.name is not None:
        server.name = body.name
    if body.container_config is not None:
        server.container_config = body.container_config
        server.gtm_container_id, server.gtm_env = parse_container_config(body.container_config)
    if body.addons is not None:
        server.addons = {**server.addons, **body.addons.model_dump(exclude_unset=True)}
        traefik_svc.write_client_config(str(server.id), server.subdomain, server.addons)
        if settings.traefik_restart_on_config_change:
            background_tasks.add_task(docker_service.restart_traefik)
    await db.commit()
    await db.refresh(server)
    return server


@router.delete("/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")

    server.status = ServerStatus.deleted
    await db.commit()

    if server.server_container_id and server.preview_container_id:
        await docker_service.stop_containers(server.server_container_id, server.preview_container_id)

    if server.dns_record_id:
        await cloudflare.delete_record(server.dns_record_id)

    traefik_svc.delete_client_config(server.subdomain)
    if settings.traefik_restart_on_config_change:
        background_tasks.add_task(docker_service.restart_traefik)


@router.post("/{server_id}/suspend", response_model=ServerResponse)
async def suspend_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.status == ServerStatus.suspended:
        return server
    server.status = ServerStatus.suspended
    await db.commit()
    if server.server_container_id and server.preview_container_id:
        await docker_service.suspend_containers(server.server_container_id, server.preview_container_id)
    await db.refresh(server)
    return server


@router.post("/{server_id}/resume", response_model=ServerResponse)
async def resume_server(
    server_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")

    containers_running = False
    if server.server_container_id and server.preview_container_id:
        await docker_service.resume_containers(server.server_container_id, server.preview_container_id)
        health = await docker_service.container_health(server.server_container_id)
        containers_running = health != "not_found"

    if not containers_running:
        srv_id, prev_id = await docker_service.start_gtm_containers(
            server.subdomain, server.container_config
        )
        server.server_container_id = srv_id
        server.preview_container_id = prev_id
        server.status = ServerStatus.provisioning
    else:
        server.status = ServerStatus.active

    await db.commit()
    if settings.traefik_restart_on_config_change:
        background_tasks.add_task(docker_service.restart_traefik)
    await db.refresh(server)
    return server


@router.get("/{server_id}/health")
async def server_health(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    server = await db.get(Server, server_id)
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")
    container_status = "unknown"
    if server.server_container_id:
        container_status = await docker_service.container_health(server.server_container_id)
    return {
        "server_id": str(server_id),
        "status": server.status,
        "container_health": container_status,
        "subdomain": server.subdomain,
    }


@router.get("/{server_id}/snippet")
async def server_snippet(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _key: ApiKey = Depends(require_api_key),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id).options(selectinload(Server.custom_domain))
    )
    server = result.scalar_one_or_none()
    if not server or server.status == ServerStatus.deleted or server.tenant_id != _key.tenant_id:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.custom_domain and server.custom_domain.status.value == "active":
        domain = server.custom_domain.domain
    else:
        domain = f"{server.subdomain}.{settings.base_domain}"

    server_url = f"https://{domain}"
    return {
        "server_url": server_url,
        "gtm_snippet": f'<script>window.dataLayer=window.dataLayer||[];(function(w,d,s,l,i){{...}})(window,document,"script","dataLayer","{server_url}");</script>',
    }
