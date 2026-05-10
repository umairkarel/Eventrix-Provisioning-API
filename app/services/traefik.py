import yaml
from pathlib import Path
from app.config import settings


def build_middleware_chain(subdomain: str, addons: dict) -> list[str]:
    chain = [f"rate-limit-{subdomain}@file"]
    if addons.get("bot_filter"):
        chain.append(f"bot-filter-{subdomain}@file")
    if addons.get("geoip"):
        chain.append(f"geoip-{subdomain}@file")
    chain.append(f"headers-{subdomain}@file")
    return chain


def generate_client_config(client_id: str, subdomain: str, addons: dict) -> str:
    rate_limit_rps = addons.get("rate_limit_rps", 50)

    middlewares: dict = {
        f"rate-limit-{subdomain}": {
            "rateLimit": {
                "average": rate_limit_rps,
                "burst": rate_limit_rps * 2,
            }
        },
        f"headers-{subdomain}": {
            "headers": {
                "customRequestHeaders": {
                    "X-Client-ID": client_id,
                }
            }
        },
    }

    if addons.get("bot_filter"):
        middlewares[f"bot-filter-{subdomain}"] = {
            "plugin": {"crawlerUserAgents": {}}
        }

    if addons.get("geoip"):
        middlewares[f"geoip-{subdomain}"] = {
            "plugin": {
                "geoip2": {"dbPath": "/etc/traefik/GeoLite2-City.mmdb"}
            }
        }

    config = {"http": {"middlewares": middlewares}}
    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def write_client_config(client_id: str, subdomain: str, addons: dict) -> None:
    content = generate_client_config(client_id, subdomain, addons)
    conf_dir = Path(settings.traefik_conf_dir)
    conf_dir.mkdir(parents=True, exist_ok=True)
    (conf_dir / f"client-{subdomain}.yml").write_text(content)


def delete_client_config(subdomain: str) -> None:
    path = Path(settings.traefik_conf_dir) / f"client-{subdomain}.yml"
    path.unlink(missing_ok=True)
