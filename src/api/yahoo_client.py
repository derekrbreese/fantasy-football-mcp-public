"""Yahoo Fantasy Sports API client with rate limiting and token refresh."""

import os
import socket
from typing import Dict

import aiohttp
from src.api.yahoo_utils import rate_limiter, response_cache

# Module-level token cache
_YAHOO_ACCESS_TOKEN = os.getenv("YAHOO_ACCESS_TOKEN")
YAHOO_API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def get_access_token() -> str:
    """Get the current access token."""
    global _YAHOO_ACCESS_TOKEN
    if _YAHOO_ACCESS_TOKEN is None:
        _YAHOO_ACCESS_TOKEN = os.getenv("YAHOO_ACCESS_TOKEN")
    return _YAHOO_ACCESS_TOKEN or ""


def set_access_token(token: str) -> None:
    """Update the access token (used after refresh)."""
    global _YAHOO_ACCESS_TOKEN
    _YAHOO_ACCESS_TOKEN = token
    os.environ["YAHOO_ACCESS_TOKEN"] = token


async def yahoo_api_call(
    endpoint: str, retry_on_auth_fail: bool = True, use_cache: bool = True
) -> Dict:
    """Make Yahoo API request with rate limiting, caching, and automatic token refresh.

    Args:
        endpoint: Yahoo API endpoint (e.g., "users;use_login=1/games")
        retry_on_auth_fail: If True, will attempt token refresh on 401 errors
        use_cache: If True, will check cache before making API call

    Returns:
        dict: JSON response from Yahoo API

    Raises:
        Exception: On API errors or authentication failures
    """
    # Check cache first (if enabled)
    if use_cache:
        cached_response = await response_cache.get(endpoint)
        if cached_response is not None:
            return cached_response

    # Apply rate limiting
    await rate_limiter.acquire()

    access_token = get_access_token()
    url = f"{YAHOO_API_BASE}/{endpoint}?format=json"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                # Cache successful response
                if use_cache:
                    await response_cache.set(endpoint, data)
                return data
            elif response.status == 401 and retry_on_auth_fail:
                # Token expired, try to refresh
                refresh_result = await refresh_yahoo_token()
                if refresh_result.get("status") == "success":
                    # Token refreshed, retry the API call with new token
                    return await yahoo_api_call(
                        endpoint, retry_on_auth_fail=False, use_cache=use_cache
                    )
                else:
                    # Refresh failed, raise the original error
                    text = await response.text()
                    raise Exception(f"Yahoo API auth failed and token refresh failed: {text[:200]}")
            else:
                text = await response.text()
                raise Exception(f"Yahoo API error {response.status}: {text[:200]}")


async def refresh_yahoo_token() -> Dict:
    """Refresh the Yahoo access token using the refresh token.

    Returns:
        dict: Status message with refresh result
            - {"status": "success", "message": "...", "expires_in": 3600}
            - {"status": "error", "message": "...", "details": "..."}
    """
    client_id = os.getenv("YAHOO_CLIENT_ID")
    client_secret = os.getenv("YAHOO_CLIENT_SECRET")
    refresh_token = os.getenv("YAHOO_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        return {"status": "error", "message": "Missing credentials in environment"}

    token_url = "https://api.login.yahoo.com/oauth2/get_token"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            async with session.post(token_url, data=data) as response:
                if response.status == 200:
                    token_data = await response.json()
                    new_access_token = token_data.get("access_token")
                    new_refresh_token = token_data.get("refresh_token", refresh_token)
                    expires_in = token_data.get("expires_in", 3600)

                    # Update global token
                    set_access_token(new_access_token)

                    # Update environment
                    if new_refresh_token != refresh_token:
                        os.environ["YAHOO_REFRESH_TOKEN"] = new_refresh_token

                    return {
                        "status": "success",
                        "message": "Token refreshed successfully",
                        "expires_in": expires_in,
                        "expires_in_hours": round(expires_in / 3600, 1),
                    }
                else:
                    error_text = await response.text()
                    return {
                        "status": "error",
                        "message": f"Failed to refresh token: {response.status}",
                        "details": error_text[:200],
                    }
    except Exception as e:
        return {"status": "error", "message": f"Error refreshing token: {str(e)}"}
