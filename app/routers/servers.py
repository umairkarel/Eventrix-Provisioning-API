import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import require_api_key
from app.database import SessionLocal, get_session
from app.models import ApiKey, Server, ServerStatus
from app.schemas import AddonsSchema, ServerCreate, ServerResponse, ServerUpdate, parse_container_config
from app.services import traefik as traefik_svc
from app.services import docker_service, cloudflare
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/servers", tags=["servers"])


# ── Background task helpers ────────────────────────────────────────────────────

async def _provision_server_bg(
    server_id: uuid.UUID,
    subdomain: str,
    container_config: str,
    traefik_restart: bool,
) -> None:
    """Create DNS record and start GTM containers after the API has returned."""
    dns_record_id: str | None = None
    # Track container IDs locally so we can stop them if the DB commit fails.
    # Cleared to None once successfully persisted — prevents double-cleanup.
    srv_id: str | None = None
    prev_id: str | None = None
    try:
        dns_record_id = await cloudflare.create_a_record(subdomain)
        async with SessionLocal() as db:
            s = await db.get(Server, server_id)
            if s:
                s.dns_record_id = dns_record_id
                await db.commit()

        if traefik_restart:
            await docker_service.restart_traefik()

        srv_id, prev_id = await docker_service.start_gtm_containers(subdomain, container_config)
        async with SessionLocal() as db:
            s = await db.get(Server, server_id)
            if s:
                s.server_container_id = srv_id
                s.preview_container_id = prev_id
                await db.commit()
        srv_id = prev_id = None  # saved; docker_events handles provisioning → active
    except Exception:
        logger.exception("Background provisioning failed for subdomain %s", subdomain)
        if srv_id and prev_id:
            # Containers started but container IDs weren't persisted — stop them
            # so they don't run orphaned with no DB reference.
            try:
                await docker_service.stop_containers(srv_id, prev_id)
            except Exception:
                logger.exception("Container cleanup failed for %s after provisioning error", subdomain)
        if dns_record_id:
            try:
                await cloudflare.delete_record(dns_record_id)
            except Exception:
                logger.exception("DNS cleanup failed for %s after provisioning error", subdomain)
        try:
            traefik_svc.delete_client_config(subdomain)
        except Exception:
            logger.exception("Traefik config cleanup failed for %s", subdomain)
        try:
            async with SessionLocal() as db:
                s = await db.get(Server, server_id)
                if s and s.status != ServerStatus.deleted:
                    s.status = ServerStatus.error
                    await db.commit()
        except Exception:
            logger.exception("Status error update failed for %s after provisioning failure", subdomain)


async def _delete_server_bg(
    server_container_id: str | None,
    preview_container_id: str | None,
    dns_record_id: str | None,
) -> None:
    """Stop containers and remove DNS record after the delete response has been sent."""
    if server_container_id and preview_container_id:
        try:
            await docker_service.stop_containers(server_container_id, preview_container_id)
        except Exception:
            logger.exception("Failed to stop containers during delete (%s, %s)", server_container_id, preview_container_id)
    if dns_record_id:
        try:
            await cloudflare.delete_record(dns_record_id)
        except Exception:
            logger.exception("Failed to delete DNS record %s", dns_record_id)


async def _suspend_server_bg(server_container_id: str, preview_container_id: str) -> None:
    """Stop containers after the suspend response has been sent."""
    try:
        await docker_service.suspend_containers(server_container_id, preview_container_id)
    except Exception:
        logger.exception("Failed to suspend containers (%s, %s)", server_container_id, preview_container_id)


async def _resume_server_bg(
    server_id: uuid.UUID,
    server_container_id: str | None,
    preview_container_id: str | None,
    subdomain: str,
    container_config: str,
) -> None:
    """Resume or re-create containers after the resume response has been sent.

    If existing containers are still present, start them; docker_events handles
    provisioning → active when their health check passes. If containers are gone,
    re-create them the same way as initial provisioning.
    """
    try:
        if server_container_id and preview_container_id:
            await docker_service.resume_containers(server_container_id, preview_container_id)
            health = await docker_service.container_health(server_container_id)
            if health != "not_found":
                return  # containers are up; docker_events will handle provisioning → active

        srv_id, prev_id = await docker_service.start_gtm_containers(subdomain, container_config)
        async with SessionLocal() as db:
            s = await db.get(Server, server_id)
            if s:
                s.server_container_id = srv_id
                s.preview_container_id = prev_id
                await db.commit()
    except Exception:
        logger.exception("Failed to resume server %s (%s)", server_id, subdomain)
        try:
            async with SessionLocal() as db:
                s = await db.get(Server, server_id)
                if s and s.status == ServerStatus.provisioning:
                    s.status = ServerStatus.error
                    await db.commit()
        except Exception:
            logger.exception("Status error update failed for %s after resume failure", subdomain)


# ── Endpoints ─────────────────────────────────────────────────────────────────

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
        traefik_svc.write_client_config(str(server.id), body.subdomain, addons_dict)
    except Exception:
        logger.exception("Failed to write Traefik config for %s", body.subdomain)
        server.status = ServerStatus.error
        await db.commit()
        raise HTTPException(status_code=500, detail="Failed to write routing config")

    background_tasks.add_task(
        _provision_server_bg,
        server.id,
        body.subdomain,
        body.container_config,
        settings.traefik_restart_on_config_change,
    )
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

    traefik_svc.delete_client_config(server.subdomain)
    if settings.traefik_restart_on_config_change:
        background_tasks.add_task(docker_service.restart_traefik)

    background_tasks.add_task(
        _delete_server_bg,
        server.server_container_id,
        server.preview_container_id,
        server.dns_record_id,
    )


@router.post("/{server_id}/suspend", response_model=ServerResponse)
async def suspend_server(
    server_id: uuid.UUID,
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(
            _suspend_server_bg,
            server.server_container_id,
            server.preview_container_id,
        )

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

    server.status = ServerStatus.provisioning
    await db.commit()

    background_tasks.add_task(
        _resume_server_bg,
        server.id,
        server.server_container_id,
        server.preview_container_id,
        server.subdomain,
        server.container_config,
    )
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
