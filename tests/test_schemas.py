import base64
from app.schemas import parse_container_config


def test_parse_valid_with_env():
    config = base64.b64encode(b"id=GTM-XXXX&env=2").decode()
    cid, env = parse_container_config(config)
    assert cid == "GTM-XXXX"
    assert env == 2


def test_parse_valid_no_env():
    config = base64.b64encode(b"id=GTM-ABCD").decode()
    cid, env = parse_container_config(config)
    assert cid == "GTM-ABCD"
    assert env is None


def test_parse_missing_id_returns_none():
    config = base64.b64encode(b"env=3&other=value").decode()
    cid, env = parse_container_config(config)
    assert cid is None
    assert env == 3


def test_parse_invalid_input_returns_none():
    cid, env = parse_container_config("!@#$%^&*()")
    assert cid is None
    assert env is None


def test_parse_empty_string_returns_none():
    cid, env = parse_container_config("")
    assert cid is None
    assert env is None
