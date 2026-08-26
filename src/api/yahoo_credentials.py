"""Request-scoped Yahoo credential handling.

The original MCP was single-user and read Yahoo tokens directly from process-wide
environment variables. That is still supported as a legacy/local-development
fallback, but production callers can bind a distinct credential set to each
async request with ``use_yahoo_credentials``.

A request context owns a mutable ``YahooCredentialSession`` holder. Token refreshes
replace the credentials on that holder, so the caller can persist the final
(rotated) refresh token after the request completes instead of losing it when the
ContextVar is reset.
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


@dataclass
class YahooCredentialSession:
    """Mutable holder for one request's current Yahoo credentials.

    Production callers should persist ``session.credentials`` after leaving
    ``use_yahoo_credentials``. If Yahoo rotates the refresh token during the
    request, the holder contains the replacement token before the context exits.
    """

    credentials: YahooCredentials


_request_session: ContextVar[YahooCredentialSession | None] = ContextVar(
    "yahoo_request_session", default=None
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
    session = _request_session.get()
    return session.credentials if session is not None else credentials_from_env()


def has_request_credentials() -> bool:
    """Whether the current async context has a user-specific Yahoo credential set."""
    return _request_session.get() is not None


def set_request_credentials(credentials: YahooCredentials) -> Token:
    """Bind credentials to the current async context and return a reset token."""
    return _request_session.set(YahooCredentialSession(credentials=credentials))


def reset_request_credentials(token: Token) -> None:
    """Restore the previous request credential context."""
    _request_session.reset(token)


def update_current_credentials(
    *, access_token: str | None = None, refresh_token: str | None = None
) -> YahooCredentials:
    """Update the active credential set without leaking one user's tokens to another.

    Request-scoped refreshes update the mutable session holder. The caller that
    owns the request can then persist ``session.credentials`` after the context
    exits. Legacy/local mode keeps the historical environment-variable behavior
    for backwards compatibility.
    """
    current = get_yahoo_credentials()
    updated = replace(
        current,
        access_token=access_token if access_token is not None else current.access_token,
        refresh_token=refresh_token if refresh_token is not None else current.refresh_token,
    )

    session = _request_session.get()
    if session is not None:
        session.credentials = updated
    else:
        os.environ["YAHOO_ACCESS_TOKEN"] = updated.access_token
        os.environ["YAHOO_REFRESH_TOKEN"] = updated.refresh_token

    return updated


@contextmanager
def use_yahoo_credentials(credentials: YahooCredentials) -> Iterator[YahooCredentialSession]:
    """Temporarily bind one user's Yahoo credentials to the current request context.

    Example production usage::

        with use_yahoo_credentials(stored_credentials) as session:
            await handle_request()
        await credential_store.save(session.credentials)

    Persisting the final session credentials is required because Yahoo may rotate
    refresh tokens during an automatic token refresh.
    """
    session = YahooCredentialSession(credentials=credentials)
    token = _request_session.set(session)
    try:
        yield session
    finally:
        reset_request_credentials(token)
