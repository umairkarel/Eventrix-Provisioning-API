from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://provisioning:provisioning@postgres:5432/provisioning"
    docker_host: str = "tcp://docker-socket-proxy:2375"
    cf_api_token: str = ""
    cf_api_token_file: str | None = None
    cf_zone_id: str = ""
    base_domain: str = ""
    vm_ip: str = ""
    traefik_conf_dir: str = "/traefik/conf.d"
    mock_cloudflare: bool = False
    traefik_restart_on_config_change: bool = False
    preview_use_sidecar: bool = True
    gtm_image: str = "gcr.io/cloud-tagging-10302018/gtm-cloud-image:stable"
    gtm_port: int = 8080
    gtm_server_mem_limit: str = "512m"
    gtm_server_nano_cpus: int = 1_000_000_000
    gtm_preview_mem_limit: str = "256m"
    gtm_preview_nano_cpus: int = 500_000_000
    gtm_proxy_mem_limit: str = "64m"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def load_token_from_file(self) -> "Settings":
        if self.cf_api_token_file and not self.cf_api_token:
            path = Path(self.cf_api_token_file)
            if path.exists():
                self.cf_api_token = path.read_text().strip()
        return self


settings = Settings()
