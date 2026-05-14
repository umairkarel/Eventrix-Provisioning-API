import asyncio
import logging
from functools import partial
import docker
from docker.errors import APIError, NotFound
from app.config import settings

logger = logging.getLogger(__name__)


def server_container_name(subdomain: str) -> str:
    return f"gtm-server-{subdomain}"


def preview_container_name(subdomain: str) -> str:
    return f"gtm-preview-{subdomain}"


def preview_proxy_container_name(subdomain: str) -> str:
    return f"gtm-preview-proxy-{subdomain}"


def _get_client() -> docker.DockerClient:
    return docker.DockerClient(base_url=settings.docker_host)


class _DockerClientCtx:
    """Thin context manager wrapper — DockerClient lacks __enter__/__exit__."""
    def __init__(self) -> None:
        self._dc = _get_client()

    def __enter__(self) -> docker.DockerClient:
        return self._dc

    def __exit__(self, *_: object) -> None:
        self._dc.close()


def _nginx_proxy_command(subdomain: str) -> list[str]:
    import base64
    nginx_conf = (
        "server {"
        "  listen 443 ssl;"
        "  ssl_certificate /etc/nginx/certs/c;"
        "  ssl_certificate_key /etc/nginx/certs/k;"
        "  location / {"
        f"    proxy_pass http://{preview_container_name(subdomain)}:{settings.gtm_port};"
        "    proxy_set_header Host $host;"
        "    proxy_set_header X-Forwarded-Proto https;"
        "  }"
        "}"
    )
    conf_b64 = base64.b64encode(nginx_conf.encode()).decode()
    script = (
        "apk add --no-cache openssl 2>/dev/null && "
        "mkdir -p /etc/nginx/certs && "
        "openssl req -x509 -nodes -newkey rsa:2048 -days 3650 "
        "-keyout /etc/nginx/certs/k -out /etc/nginx/certs/c -subj '/CN=p' 2>/dev/null && "
        f"echo '{conf_b64}' | base64 -d > /etc/nginx/conf.d/default.conf && "
        "nginx -g 'daemon off;'"
    )
    return ["sh", "-c", script]


def _start_gtm_containers_sync(
    subdomain: str,
    container_config: str,
) -> tuple[str, str]:
    server = None
    preview = None
    proxy = None

    with _DockerClientCtx() as dc:
        try:
            server = dc.containers.run(
                image=settings.gtm_image,
                name=server_container_name(subdomain),
                environment={
                    "CONTAINER_CONFIG": container_config,
                    "PREVIEW_SERVER_URL": (
                        f"https://{preview_proxy_container_name(subdomain)}"
                        if settings.preview_use_sidecar
                        else f"https://preview-{subdomain}.{settings.base_domain}"
                    ),
                    "PORT": str(settings.gtm_port),
                    **({"NODE_TLS_REJECT_UNAUTHORIZED": "0"} if settings.preview_use_sidecar else {}),
                },
                network="traefik_public",
                restart_policy={"Name": "unless-stopped"},
                mem_limit=settings.gtm_server_mem_limit,
                nano_cpus=settings.gtm_server_nano_cpus,
                healthcheck={
                    "test": ["CMD", "/nodejs/bin/node", "-e", f"require('http').get('http://localhost:{settings.gtm_port}/healthz', r => process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"],
                    "interval": 30_000_000_000,
                    "timeout": 10_000_000_000,
                    "retries": 3,
                    "start_period": 30_000_000_000,
                },
                detach=True,
            )

            try:
                internal = dc.networks.get("gtm_internal")
            except docker.errors.NotFound:
                internal = dc.networks.create("gtm_internal", driver="bridge", internal=True)
            internal.connect(server)

            preview = dc.containers.run(
                image=settings.gtm_image,
                name=preview_container_name(subdomain),
                environment={
                    "CONTAINER_CONFIG": container_config,
                    "RUN_AS_PREVIEW_SERVER": "true",
                    "PORT": str(settings.gtm_port),
                },
                network="traefik_public",
                restart_policy={"Name": "unless-stopped"},
                mem_limit=settings.gtm_preview_mem_limit,
                nano_cpus=settings.gtm_preview_nano_cpus,
                healthcheck={
                    "test": ["CMD", "/nodejs/bin/node", "-e", f"require('http').get('http://localhost:{settings.gtm_port}/healthz', r => process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"],
                    "interval": 30_000_000_000,
                    "timeout": 10_000_000_000,
                    "retries": 3,
                    "start_period": 30_000_000_000,
                },
                detach=True,
            )
            internal.connect(preview)

            if settings.preview_use_sidecar:
                proxy = dc.containers.run(
                    image="nginx:alpine",
                    name=preview_proxy_container_name(subdomain),
                    command=_nginx_proxy_command(subdomain),
                    network="traefik_public",
                    restart_policy={"Name": "unless-stopped"},
                    mem_limit=settings.gtm_proxy_mem_limit,
                    detach=True,
                )

            return server.id, preview.id

        except Exception:
            for c in [proxy, preview, server]:
                if c is not None:
                    try:
                        c.stop(timeout=5)
                        c.remove()
                    except Exception:
                        logger.exception("Failed to clean up container %s after provisioning error", c.id)
            raise


def _stop_containers_sync(server_id: str, preview_id: str) -> None:
    with _DockerClientCtx() as dc:
        if settings.preview_use_sidecar:
            try:
                server_c = dc.containers.get(server_id)
                subdomain = server_c.name.lstrip("/").removeprefix("gtm-server-")
                proxy = dc.containers.get(preview_proxy_container_name(subdomain))
                proxy.stop(timeout=10)
                proxy.remove()
            except (NotFound, APIError):
                pass

        for cid in [server_id, preview_id]:
            try:
                c = dc.containers.get(cid)
                c.stop(timeout=10)
                c.remove()
            except (NotFound, APIError):
                pass


def _suspend_containers_sync(server_id: str, preview_id: str) -> None:
    with _DockerClientCtx() as dc:
        if settings.preview_use_sidecar:
            try:
                server_c = dc.containers.get(server_id)
                subdomain = server_c.name.lstrip("/").removeprefix("gtm-server-")
                dc.containers.get(preview_proxy_container_name(subdomain)).stop(timeout=10)
            except (NotFound, APIError):
                pass
        for cid in [server_id, preview_id]:
            try:
                dc.containers.get(cid).stop(timeout=10)
            except (NotFound, APIError):
                pass


def _resume_containers_sync(server_id: str, preview_id: str) -> None:
    with _DockerClientCtx() as dc:
        for cid in [server_id, preview_id]:
            try:
                dc.containers.get(cid).start()
            except (NotFound, APIError):
                pass
        if settings.preview_use_sidecar:
            try:
                server_c = dc.containers.get(server_id)
                subdomain = server_c.name.lstrip("/").removeprefix("gtm-server-")
                dc.containers.get(preview_proxy_container_name(subdomain)).start()
            except (NotFound, APIError):
                pass


def _container_health_sync(server_id: str) -> str:
    """Returns container status string: 'healthy', 'unhealthy', 'starting', 'running', 'exited', 'unknown', or 'not_found'."""
    with _DockerClientCtx() as dc:
        try:
            c = dc.containers.get(server_id)
            c.reload()
            state = c.attrs.get("State", {})
            if state.get("Status") not in ("running",):
                return state.get("Status", "unknown")
            return state.get("Health", {}).get("Status", "unknown")
        except NotFound:
            return "not_found"


async def start_gtm_containers(
    subdomain: str,
    container_config: str,
) -> tuple[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_start_gtm_containers_sync, subdomain, container_config),
    )


async def stop_containers(server_id: str, preview_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_stop_containers_sync, server_id, preview_id))


async def suspend_containers(server_id: str, preview_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_suspend_containers_sync, server_id, preview_id))


async def resume_containers(server_id: str, preview_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_resume_containers_sync, server_id, preview_id))


async def container_health(server_id: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_container_health_sync, server_id))


def _restart_traefik_sync() -> None:
    with _DockerClientCtx() as dc:
        containers = dc.containers.list(filters={"label": "com.docker.compose.service=traefik"})
        if containers:
            containers[0].restart(timeout=10)


async def restart_traefik() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _restart_traefik_sync)


def _ensure_sidecar_running_sync(subdomain: str) -> None:
    with _DockerClientCtx() as dc:
        try:
            proxy = dc.containers.get(preview_proxy_container_name(subdomain))
            if proxy.status != "running":
                proxy.start()
                logger.info("Restarted stopped sidecar %s", proxy.name)
        except NotFound:
            logger.warning("Sidecar container %s not found — skipping", preview_proxy_container_name(subdomain))
        except APIError as exc:
            logger.error("Failed to start sidecar %s: %s", preview_proxy_container_name(subdomain), exc)


async def ensure_sidecar_running(subdomain: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_ensure_sidecar_running_sync, subdomain))
