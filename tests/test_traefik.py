import yaml
from app.services.traefik import generate_client_config, build_middleware_chain


def test_middleware_chain_no_addons():
    addons = {"geoip": False, "bot_filter": False, "rate_limit_rps": 50}
    chain = build_middleware_chain("acme", addons)
    assert chain == ["rate-limit-acme@file", "headers-acme@file"]


def test_middleware_chain_all_addons():
    addons = {"geoip": True, "bot_filter": True, "rate_limit_rps": 50}
    chain = build_middleware_chain("acme", addons)
    assert chain == [
        "rate-limit-acme@file",
        "bot-filter-acme@file",
        "geoip-acme@file",
        "headers-acme@file",
    ]


def test_generate_config_has_rate_limit():
    config_str = generate_client_config("client-id-123", "acme", {"geoip": False, "bot_filter": False, "rate_limit_rps": 75})
    config = yaml.safe_load(config_str)
    rate_limit = config["http"]["middlewares"]["rate-limit-acme"]["rateLimit"]
    assert rate_limit["average"] == 75
    assert rate_limit["burst"] == 150


def test_generate_config_has_headers_with_client_id():
    config_str = generate_client_config("client-id-123", "acme", {"geoip": False, "bot_filter": False, "rate_limit_rps": 50})
    config = yaml.safe_load(config_str)
    headers = config["http"]["middlewares"]["headers-acme"]["headers"]["customRequestHeaders"]
    assert headers["X-Client-ID"] == "client-id-123"


def test_generate_config_bot_filter_present_when_enabled():
    config_str = generate_client_config("id", "acme", {"geoip": False, "bot_filter": True, "rate_limit_rps": 50})
    config = yaml.safe_load(config_str)
    assert "bot-filter-acme" in config["http"]["middlewares"]


def test_generate_config_geoip_absent_when_disabled():
    config_str = generate_client_config("id", "acme", {"geoip": False, "bot_filter": False, "rate_limit_rps": 50})
    config = yaml.safe_load(config_str)
    assert "geoip-acme" not in config["http"]["middlewares"]
