import asyncio
import logging
from functools import partial
import docker
from docker.errors import APIError, NotFound
from app.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> docker.DockerClient:
    return docker.DockerClient(base_url=settings.docker_host)


def _start_gtm_containers_sync(
    subdomain: str,
    base_domain: str,
    container_config: str,
    middlewares: list[str],
) -> tuple[str, str]:
    middleware_str = ",".join(middlewares)
    server = None
    preview = None

    with _get_client() as dc:
        try:
            server = dc.containers.run(
                image="gcr.io/cloud-tagging-10302018/gtm-cloud-image:stable",
                name=f"gtm-server-{subdomain}",
                environment={
                    "CONTAINER_CONFIG": container_config,
                    "PREVIEW_SERVER_URL": f"https://preview-{subdomain}.{base_domain}",
                    "PORT": "8080",
                },
                labels={
                    "traefik.enable": "true",
                    f"traefik.http.routers.gtm-{subdomain}.rule": f"Host(`{subdomain}.{base_domain}`)",
                    f"traefik.http.routers.gtm-{subdomain}.entrypoints": "websecure",
                    f"traefik.http.routers.gtm-{subdomain}.tls.certresolver": "cloudflare",
                    f"traefik.http.routers.gtm-{subdomain}.tls.domains[0].main": base_domain,
                    f"traefik.http.routers.gtm-{subdomain}.tls.domains[0].sans": f"*.{base_domain}",
                    f"traefik.http.routers.gtm-{subdomain}.middlewares": middleware_str,
                    f"traefik.http.services.gtm-{subdomain}.loadbalancer.server.port": "8080",
                    "traefik.docker.network": "traefik_public",
                },
                network="traefik_public",
                restart_policy={"Name": "unless-stopped"},
                mem_limit="512m",
                nano_cpus=1_000_000_000,
                healthcheck={
                    "test": ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8080/healthz"],
                    "interval": 30_000_000_000,
                    "timeout": 10_000_000_000,
                    "retries": 3,
                    "start_period": 30_000_000_000,
                },
                detach=True,
            )

            internal = dc.networks.get("gtm_internal")
            internal.connect(server)

            preview = dc.containers.run(
                image="gcr.io/cloud-tagging-10302018/gtm-cloud-image:stable",
                name=f"gtm-preview-{subdomain}",
                environment={
                    "CONTAINER_CONFIG": container_config,
                    "RUN_AS_PREVIEW_SERVER": "true",
                    "PORT": "8080",
                },
                labels={
                    "traefik.enable": "true",
                    f"traefik.http.routers.gtm-preview-{subdomain}.rule": f"Host(`preview-{subdomain}.{base_domain}`)",
                    f"traefik.http.routers.gtm-preview-{subdomain}.entrypoints": "websecure",
                    f"traefik.http.routers.gtm-preview-{subdomain}.tls.certresolver": "cloudflare",
                    f"traefik.http.services.gtm-preview-{subdomain}.loadbalancer.server.port": "8080",
                    "traefik.docker.network": "traefik_public",
                },
                network="traefik_public",
                restart_policy={"Name": "unless-stopped"},
                mem_limit="256m",
                nano_cpus=500_000_000,
                healthcheck={
                    "test": ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8080/healthz"],
                    "interval": 30_000_000_000,
                    "timeout": 10_000_000_000,
                    "retries": 3,
                    "start_period": 30_000_000_000,
                },
                detach=True,
            )
            internal.connect(preview)

            return server.id, preview.id

        except Exception:
            for c in [preview, server]:
                if c is not None:
                    try:
                        c.stop(timeout=5)
                        c.remove()
                    except Exception:
                        logger.exception("Failed to clean up container %s after provisioning error", c.id)
            raise


def _stop_containers_sync(server_id: str, preview_id: str) -> None:
    with _get_client() as dc:
        for cid in [server_id, preview_id]:
            try:
                c = dc.containers.get(cid)
                c.stop(timeout=10)
                c.remove()
            except (NotFound, APIError):
                pass


def _suspend_containers_sync(server_id: str, preview_id: str) -> None:
    with _get_client() as dc:
        for cid in [server_id, preview_id]:
            try:
                dc.containers.get(cid).stop(timeout=10)
            except (NotFound, APIError):
                pass


def _resume_containers_sync(server_id: str, preview_id: str) -> None:
    with _get_client() as dc:
        for cid in [server_id, preview_id]:
            try:
                dc.containers.get(cid).start()
            except (NotFound, APIError):
                pass


def _container_health_sync(server_id: str) -> str:
    """Returns container status string: 'healthy', 'unhealthy', 'starting', 'running', 'exited', 'unknown', or 'not_found'."""
    with _get_client() as dc:
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
    base_domain: str,
    container_config: str,
    middlewares: list[str],
) -> tuple[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_start_gtm_containers_sync, subdomain, base_domain, container_config, middlewares),
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
