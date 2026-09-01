"""Unit tests for src/api/yahoo_client.py - Yahoo API client functionality."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.yahoo_client import (
    get_access_token,
    refresh_yahoo_token,
    set_access_token,
    yahoo_api_call,
)


class TestTokenManagement:
    """Test token get/set operations."""

    def test_get_access_token_from_env(self, mock_env_vars):
        """Test getting access token from environment."""
        token = get_access_token()
        assert token == "test_access_token_12345"

    def test_set_access_token(self, mock_env_vars):
        """Test setting new access token."""
        new_token = "new_test_token_99999"
        set_access_token(new_token)
        assert get_access_token() == new_token
        assert os.environ["YAHOO_ACCESS_TOKEN"] == new_token

    def test_get_access_token_when_none(self, monkeypatch):
        """Test getting access token when not set returns empty string."""
        monkeypatch.delenv("YAHOO_ACCESS_TOKEN", raising=False)
        # Reset module-level token
        from src.api import yahoo_client

        yahoo_client._YAHOO_ACCESS_TOKEN = None
        token = get_access_token()
        assert token == ""


class TestYahooApiCall:
    """Test Yahoo API call functionality."""

    @pytest.mark.asyncio
    async def test_yahoo_api_call_success(
        self, mock_env_vars, mock_rate_limiter, mock_response_cache
    ):
        """Test successful Yahoo API call."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"test": "data"})

        with (
            patch("src.api.yahoo_client.rate_limiter", mock_rate_limiter),
            patch("src.api.yahoo_client.response_cache", mock_response_cache),
            patch("aiohttp.ClientSession") as mock_session_class,
        ):
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )
            mock_session_class.return_value = mock_session

            result = await yahoo_api_call("test/endpoint")

            assert result == {"test": "data"}
            mock_rate_limiter.acquire.assert_called_once()
            mock_response_cache.ttl_for_endpoint.assert_called_once_with("test/endpoint")
            mock_response_cache.set.assert_called_once_with(
                "test/endpoint",
                {"test": "data"},
                ttl=mock_response_cache.ttl_for_endpoint.return_value,
            )

    @pytest.mark.asyncio
    async def test_yahoo_api_call_uses_cache(
        self, mock_env_vars, mock_rate_limiter, mock_response_cache
    ):
        """Test that cached responses are returned without API call."""
        cached_data = {"cached": "response"}
        mock_response_cache.get = AsyncMock(return_value=cached_data)

        with (
            patch("src.api.yahoo_client.rate_limiter", mock_rate_limiter),
            patch("src.api.yahoo_client.response_cache", mock_response_cache),
        ):
            result = await yahoo_api_call("test/endpoint")

            assert result == cached_data
            mock_response_cache.get.assert_called_once_with("test/endpoint")
            # Should not call rate limiter if cache hit
            mock_rate_limiter.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_yahoo_api_call_401_triggers_refresh(
        self, mock_env_vars, mock_rate_limiter, mock_response_cache
    ):
        """Test that 401 error triggers token refresh and retry."""
        # First call returns 401, second call succeeds
        mock_response_401 = AsyncMock()
        mock_response_401.status = 401
        mock_response_401.text = AsyncMock(return_value="Unauthorized")

        mock_response_200 = AsyncMock()
        mock_response_200.status = 200
        mock_response_200.json = AsyncMock(return_value={"success": "data"})

        call_count = 0

        def get_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock = AsyncMock()
                mock.__aenter__.return_value = mock_response_401
                mock.__aexit__.return_value = None
                return mock
            else:
                mock = AsyncMock()
                mock.__aenter__.return_value = mock_response_200
                mock.__aexit__.return_value = None
                return mock

        with (
            patch("src.api.yahoo_client.rate_limiter", mock_rate_limiter),
            patch("src.api.yahoo_client.response_cache", mock_response_cache),
            patch(
                "src.api.yahoo_client.refresh_yahoo_token",
                AsyncMock(return_value={"status": "success"}),
            ),
            patch("aiohttp.ClientSession") as mock_session_class,
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None
            mock_session.get = get_response
            mock_session_class.return_value = mock_session

            result = await yahoo_api_call("test/endpoint")

            assert result == {"success": "data"}
            assert call_count == 2  # Initial call + retry after refresh

    @pytest.mark.asyncio
    async def test_yahoo_api_call_500_raises_exception(
        self, mock_env_vars, mock_rate_limiter, mock_response_cache
    ):
        """Test that non-401 errors raise exceptions."""

        # Create proper async context manager classes
        class MockResponse:
            def __init__(self):
                self.status = 500

            async def text(self):
                return "Internal Server Error"

        class MockGetContext:
            async def __aenter__(self):
                return MockResponse()

            async def __aexit__(self, *args):
                return None

        class MockSession:
            def get(self, *args, **kwargs):
                return MockGetContext()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        # Ensure cache returns None
        mock_response_cache.get = AsyncMock(return_value=None)

        with (
            patch("src.api.yahoo_client.rate_limiter", mock_rate_limiter),
            patch("src.api.yahoo_client.response_cache", mock_response_cache),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=MockSession()),
        ):
            with pytest.raises(Exception, match="Yahoo API error 500"):
                await yahoo_api_call("test/endpoint")

    @pytest.mark.asyncio
    async def test_yahoo_api_call_cache_disabled(
        self, mock_env_vars, mock_rate_limiter, mock_response_cache
    ):
        """Test API call with caching disabled."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"test": "data"})

        with (
            patch("src.api.yahoo_client.rate_limiter", mock_rate_limiter),
            patch("src.api.yahoo_client.response_cache", mock_response_cache),
            patch("aiohttp.ClientSession") as mock_session_class,
        ):
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )
            mock_session_class.return_value = mock_session

            result = await yahoo_api_call("test/endpoint", use_cache=False)

            assert result == {"test": "data"}
            mock_response_cache.get.assert_not_called()
            mock_response_cache.set.assert_not_called()


class TestRefreshYahooToken:
    """Test Yahoo OAuth token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, mock_env_vars):
        """Test successful token refresh."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 3600,
            }
        )

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )
            mock_session_class.return_value = mock_session

            result = await refresh_yahoo_token()

            assert result["status"] == "success"
            assert result["expires_in"] == 3600
            assert result["expires_in_hours"] == 1.0
            assert get_access_token() == "new_access_token"
            assert os.environ["YAHOO_REFRESH_TOKEN"] == "new_refresh_token"

    @pytest.mark.asyncio
    async def test_refresh_token_missing_credentials(self, monkeypatch):
        """Test token refresh with missing credentials."""
        monkeypatch.delenv("YAHOO_CLIENT_ID", raising=False)

        result = await refresh_yahoo_token()

        assert result["status"] == "error"
        assert "Missing Yahoo credentials" in result["message"]

    @pytest.mark.asyncio
    async def test_refresh_token_api_error(self, mock_env_vars):
        """Test token refresh when API returns error."""
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Invalid grant")

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )
            mock_session_class.return_value = mock_session

            result = await refresh_yahoo_token()

            assert result["status"] == "error"
            assert "Failed to refresh token" in result["message"]
            assert "400" in result["message"]

    @pytest.mark.asyncio
    async def test_refresh_token_network_error(self, mock_env_vars):
        """Test token refresh when network error occurs."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session_class.side_effect = Exception("Network error")

            result = await refresh_yahoo_token()

            assert result["status"] == "error"
            assert "Error refreshing token" in result["message"]
            assert "Network error" in result["message"]
