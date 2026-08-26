"""Tests for request-scoped Yahoo credential isolation."""

import os

from src.api.yahoo_client import _cache_key, get_access_token, set_access_token
from src.api.yahoo_credentials import YahooCredentials, use_yahoo_credentials
from src.api.yahoo_utils import ResponseCache


def _credentials(user_id: str, token: str) -> YahooCredentials:
    return YahooCredentials(
        access_token=token,
        refresh_token=f"refresh-{user_id}",
        client_id="client-id",
        client_secret="client-secret",
        user_id=user_id,
    )


def test_request_credentials_do_not_mutate_environment(monkeypatch):
    monkeypatch.setenv("YAHOO_ACCESS_TOKEN", "legacy-token")

    with use_yahoo_credentials(_credentials("user-a", "token-a")) as session:
        assert get_access_token() == "token-a"
        set_access_token("token-a-refreshed")
        assert get_access_token() == "token-a-refreshed"
        assert session.credentials.access_token == "token-a-refreshed"
        assert os.environ["YAHOO_ACCESS_TOKEN"] == "legacy-token"

    assert get_access_token() == "legacy-token"
    assert session.credentials.access_token == "token-a-refreshed"


def test_rotated_refresh_token_survives_context_exit(monkeypatch):
    monkeypatch.setenv("YAHOO_ACCESS_TOKEN", "legacy-token")
    original = _credentials("user-a", "token-a")

    from src.api.yahoo_credentials import update_current_credentials

    with use_yahoo_credentials(original) as session:
        update_current_credentials(
            access_token="token-a-new",
            refresh_token="refresh-user-a-rotated",
        )
        assert session.credentials.refresh_token == "refresh-user-a-rotated"

    # The caller can persist this value after request cleanup.
    assert session.credentials.access_token == "token-a-new"
    assert session.credentials.refresh_token == "refresh-user-a-rotated"


def test_request_credentials_are_restored_after_nested_contexts(monkeypatch):
    monkeypatch.setenv("YAHOO_ACCESS_TOKEN", "legacy-token")

    with use_yahoo_credentials(_credentials("user-a", "token-a")):
        assert get_access_token() == "token-a"
        with use_yahoo_credentials(_credentials("user-b", "token-b")):
            assert get_access_token() == "token-b"
        assert get_access_token() == "token-a"

    assert get_access_token() == "legacy-token"


def test_cache_keys_are_namespaced_by_user():
    endpoint = "users;use_login=1/games"

    with use_yahoo_credentials(_credentials("user-a", "token-a")):
        key_a = _cache_key(endpoint)

    with use_yahoo_credentials(_credentials("user-b", "token-b")):
        key_b = _cache_key(endpoint)

    assert key_a != key_b
    assert "user-a" in key_a
    assert "user-b" in key_b


def test_cache_ttl_uses_endpoint_not_user_namespace():
    cache = ResponseCache()
    endpoint = "league/123/roster"

    # A malicious/unlucky user id containing 'draft' must not change roster TTL.
    with use_yahoo_credentials(_credentials("draft-user", "token-a")):
        key = _cache_key(endpoint)

    assert "draft-user" in key
    assert cache.ttl_for_endpoint(endpoint) == cache.default_ttls["roster"]
    assert cache.ttl_for_endpoint(endpoint) != cache.default_ttls["draft"]
