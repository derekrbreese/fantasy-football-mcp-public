"""Yahoo Fantasy Sports API client with rate limiting and token refresh."""

from __future__ import annotations

import hashlib
import socket
from typing import Dict

import aiohttp

from src.api.yahoo_credentials import (
    get_yahoo_credentials,
    has_request_credentials,
    update_current_credentials,
)
from src.api.yahoo_utils import rate_limiter, response_cache

YAHOO_API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

# Yahoo's 401s carry an oauth_problem that distinguishes recoverable from
# unrecoverable auth failures. additional_authorization_required means the
# token is valid but the app itself is not entitled to the Fantasy Sports API.
NOT_PROVISIONED_ERROR = (
    "Your Yahoo app is not provisioned for the Fantasy Sports API "
    '(oauth_problem="additional_authorization_required"). This is not a '
    "token problem - refreshing will not help. Apply at "
    "https://sports.yahoo.com/developer/access/ and include your existing "
    "Client ID so approval is attached to the app you already have."
)


def get_access_token() -> str:
    """Return the current request's Yahoo access token."""
    return get_yahoo_credentials().access_token


def set_access_token(token: str) -> None:
    """Update the current request's access token.

    In legacy/local mode this retains the historical environment-variable
    behavior. In request-scoped mode the token stays isolated to that request.
    """
    update_current_credentials(access_token=token)


def _cache_key(endpoint: str) -> str:
    """Namespace cached Yahoo responses for request-scoped/multi-user calls.

    Local single-user mode keeps the historical key format for backwards
    compatibility. Production callers that bind request credentials are always
    namespaced so one Yahoo user's cached league data cannot leak to another.
    """
    if not has_request_credentials():
        return endpoint

    credentials = get_yahoo_credentials()
    namespace = credentials.user_id
    if not namespace:
        token_fingerprint = hashlib.sha256(credentials.access_token.encode("utf-8")).hexdigest()[:16]
        namespace = f"token:{token_fingerprint}"
    return f"yahoo:{namespace}:{endpoint}"


async def yahoo_api_call(
    endpoint: str, retry_on_auth_fail: bool = True, use_cache: bool = True
) -> Dict:
    """Make a Yahoo API request with rate limiting, scoped caching, and refresh."""
    cache_key = _cache_key(endpoint)

    if use_cache:
        cached_response = await response_cache.get(cache_key)
        if cached_response is not None:
            return cached_response

    await rate_limiter.acquire()

    credentials = get_yahoo_credentials()
    if not credentials.access_token:
        raise Exception("Missing Yahoo access token for the current user/request")

    url = f"{YAHOO_API_BASE}/{endpoint}?format=json"
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Accept": "application/json",
    }

    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if use_cache:
                    await response_cache.set(cache_key, data)
                return data

            if response.status == 401:
                text = await response.text()
                if "additional_authorization_required" in text:
                    raise Exception(NOT_PROVISIONED_ERROR)

                if retry_on_auth_fail:
                    refresh_result = await refresh_yahoo_token()
                    if refresh_result.get("status") == "success":
                        return await yahoo_api_call(
                            endpoint, retry_on_auth_fail=False, use_cache=use_cache
                        )
                    raise Exception(
                        f"Yahoo API auth failed and token refresh failed: {text[:200]}"
                    )

                raise Exception(f"Yahoo API error 401 after token refresh: {text[:200]}")

            text = await response.text()
            raise Exception(f"Yahoo API error {response.status}: {text[:200]}")


async def refresh_yahoo_token() -> Dict:
    """Refresh the current request's Yahoo access token."""
    credentials = get_yahoo_credentials()

    if not all(
        [credentials.client_id, credentials.client_secret, credentials.refresh_token]
    ):
        return {
            "status": "error",
            "message": "Missing Yahoo credentials for the current user/request",
        }

    token_url = "https://api.login.yahoo.com/oauth2/get_token"
    data = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            async with session.post(token_url, data=data) as response:
                if response.status == 200:
                    token_data = await response.json()
                    new_access_token = token_data.get("access_token")
                    new_refresh_token = token_data.get(
                        "refresh_token", credentials.refresh_token
                    )
                    expires_in = token_data.get("expires_in", 3600)

                    if not new_access_token:
                        return {
                            "status": "error",
                            "message": "Yahoo refresh response did not include an access token",
                        }

                    update_current_credentials(
                        access_token=new_access_token,
                        refresh_token=new_refresh_token,
                    )

                    return {
                        "status": "success",
                        "message": "Token refreshed successfully",
                        "expires_in": expires_in,
                        "expires_in_hours": round(expires_in / 3600, 1),
                    }

                error_text = await response.text()
                return {
                    "status": "error",
                    "message": f"Failed to refresh token: {response.status}",
                    "details": error_text[:200],
                }
    except Exception as exc:
        return {"status": "error", "message": f"Error refreshing token: {exc}"}
