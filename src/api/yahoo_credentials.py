"""Request-scoped Yahoo credential handling.

The original MCP was single-user and read Yahoo tokens directly from process-wide
environment variables.  That is still supported as a legacy/local-development
fallback, but production callers can bind a distinct credential set to each
async request with ``use_yahoo_credentials``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Iterator


@dataclass(frozen=True)
class YahooCredentials:
    """Credentials required to call and refresh the Yahoo Fantasy API."""

    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    user_id: str | None = None


_request_credentials: ContextVar[YahooCredentials | None] = ContextVar(
    "yahoo_request_credentials", default=None
)


def credentials_from_env() -> YahooCredentials:
    """Build the legacy single-user credential set from environment variables."""
    return YahooCredentials(
        access_token=os.getenv("YAHOO_ACCESS_TOKEN", ""),
        refresh_token=os.getenv("YAHOO_REFRESH_TOKEN", ""),
        client_id=os.getenv("YAHOO_CLIENT_ID", ""),
        client_secret=os.getenv("YAHOO_CLIENT_SECRET", ""),
        user_id=os.getenv("YAHOO_GUID") or None,
    )


def get_yahoo_credentials() -> YahooCredentials:
    """Return request-scoped credentials, falling back to legacy environment config."""
    return _request_credentials.get() or credentials_from_env()


def has_request_credentials() -> bool:
    """Whether the current async context has a user-specific Yahoo credential set."""
    return _request_credentials.get() is not None


def set_request_credentials(credentials: YahooCredentials) -> Token:
    """Bind credentials to the current async context and return a reset token."""
    return _request_credentials.set(credentials)


def reset_request_credentials(token: Token) -> None:
    """Restore the previous request credential context."""
    _request_credentials.reset(token)


def update_current_credentials(
    *, access_token: str | None = None, refresh_token: str | None = None
) -> YahooCredentials:
    """Update the active credential set without leaking one user's tokens to another.

    Request-scoped credentials remain in the ContextVar.  Legacy/local mode keeps
    the historical environment-variable behavior for backwards compatibility.
    """
    current = get_yahoo_credentials()
    updated = replace(
        current,
        access_token=access_token if access_token is not None else current.access_token,
        refresh_token=refresh_token if refresh_token is not None else current.refresh_token,
    )

    if has_request_credentials():
        _request_credentials.set(updated)
    else:
        os.environ["YAHOO_ACCESS_TOKEN"] = updated.access_token
        os.environ["YAHOO_REFRESH_TOKEN"] = updated.refresh_token

    return updated


@contextmanager
def use_yahoo_credentials(credentials: YahooCredentials) -> Iterator[YahooCredentials]:
    """Temporarily bind one user's Yahoo credentials to the current request context."""
    token = set_request_credentials(credentials)
    try:
        yield credentials
    finally:
        reset_request_credentials(token)
