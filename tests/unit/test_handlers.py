"""Unit tests for MCP tool handlers."""

from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.admin_handlers import (
    handle_ff_clear_cache,
    handle_ff_get_api_status,
    handle_ff_refresh_token,
)


class TestAdminHandlers:
    """Test admin and system management handlers."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Test successful token refresh."""
        mock_refresh = AsyncMock(
            return_value={
                "status": "success",
                "message": "Token refreshed successfully",
                "expires_in": 3600,
            }
        )

        with patch("src.handlers.admin_handlers.refresh_yahoo_token", mock_refresh):
            result = await handle_ff_refresh_token({})

            assert result["status"] == "success"
            assert "expires_in" in result
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        """Test failed token refresh."""
        mock_refresh = AsyncMock(return_value={"status": "error", "message": "Missing credentials"})

        with patch("src.handlers.admin_handlers.refresh_yahoo_token", mock_refresh):
            result = await handle_ff_refresh_token({})

            assert result["status"] == "error"
            assert "Missing credentials" in result["message"]

    @pytest.mark.asyncio
    async def test_get_api_status(self, mock_rate_limiter, mock_response_cache):
        """Test getting API status."""
        with (
            patch("src.handlers.admin_handlers.rate_limiter", mock_rate_limiter),
            patch("src.handlers.admin_handlers.response_cache", mock_response_cache),
        ):
            result = await handle_ff_get_api_status({})

            assert "rate_limit" in result
            assert "cache" in result
            assert result["rate_limit"]["requests_remaining"] == 850
            assert result["cache"]["hits"] == 245

    @pytest.mark.asyncio
    async def test_clear_cache_full(self, mock_response_cache):
        """Test clearing entire cache."""
        with patch("src.handlers.admin_handlers.response_cache", mock_response_cache):
            result = await handle_ff_clear_cache({})

            assert result["status"] == "success"
            assert "completely" in result["message"]
            mock_response_cache.clear.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_clear_cache_with_pattern(self, mock_response_cache):
        """Test clearing cache with pattern."""
        with patch("src.handlers.admin_handlers.response_cache", mock_response_cache):
            result = await handle_ff_clear_cache({"pattern": "league/*"})

            assert result["status"] == "success"
            assert "league/*" in result["message"]
            mock_response_cache.clear.assert_called_once_with("league/*")


class TestDraftRankings:
    @pytest.mark.asyncio
    async def test_rankings_include_yahoo_injury_status(self):
        from fantasy_football_multi_league import get_draft_rankings

        response = {
            "fantasy_content": {
                "league": [
                    {"league_key": "461.l.61410"},
                    {
                        "players": {
                            "0": {
                                "player": [
                                    [
                                        {"name": {"full": "Josh Allen"}},
                                        {"editorial_team_abbr": "BUF"},
                                        {"display_position": "QB"},
                                        {"status": "IR-R"},
                                        {"status_full": "Injured Reserve"},
                                        {
                                            "draft_analysis": {
                                                "average_pick": "8.2",
                                                "percent_drafted": 100,
                                            }
                                        },
                                    ]
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            }
        }

        with patch(
            "fantasy_football_multi_league.yahoo_api_call",
            AsyncMock(return_value=response),
        ):
            result = await get_draft_rankings("461.l.61410", count=1)

        assert result[0]["injury_status"] == "IR"
        assert result[0]["injury_note"] == "Injured Reserve"
        assert result[0]["rank"] == 1


class TestLeagueHandlers:
    """Test league-level handlers."""

    def test_extracts_yahoo_roster_positions(self):
        from src.handlers.league_handlers import extract_yahoo_roster_positions

        payload = {
            "fantasy_content": {
                "league": [
                    {"league_key": "461.l.61410"},
                    {
                        "settings": [
                            {
                                "roster_positions": [
                                    {"roster_position": {"position": "QB", "count": 1}},
                                    {
                                        "roster_position": {
                                            "position": "Q/W/R/T",
                                            "position_type": "F",
                                            "count": 1,
                                        }
                                    },
                                    {
                                        "roster_position": {
                                            "position": "BN",
                                            "position_type": "BN",
                                            "count": 6,
                                        }
                                    },
                                ]
                            }
                        ]
                    },
                ]
            }
        }

        assert extract_yahoo_roster_positions(payload) == [
            {"position": "QB", "position_type": None, "count": 1},
            {"position": "SUPERFLEX", "position_type": "F", "count": 1},
            {"position": "BN", "position_type": "BN", "count": 6},
        ]

    @pytest.mark.asyncio
    async def test_get_leagues_error_handling(self):
        """Test error handling when league_key is missing."""
        from src.handlers.league_handlers import handle_ff_get_league_info

        result = await handle_ff_get_league_info({})
        assert "error" in result
        assert "league_key is required" in result["error"]

    @pytest.mark.asyncio
    async def test_get_standings_error_handling(self):
        """Test error handling for standings without league_key."""
        from src.handlers.league_handlers import handle_ff_get_standings

        result = await handle_ff_get_standings({})
        assert "error" in result
        assert "league_key is required" in result["error"]

    @pytest.mark.asyncio
    async def test_get_teams_error_handling(self):
        """Test error handling for teams without league_key."""
        from src.handlers.league_handlers import handle_ff_get_teams

        result = await handle_ff_get_teams({})
        assert "error" in result
        assert "league_key is required" in result["error"]

    @pytest.mark.asyncio
    async def test_get_standings_parses_correctly(self, mock_yahoo_standings_response):
        """Test standings parsing logic."""
        from src.handlers.league_handlers import handle_ff_get_standings

        mock_api_call = AsyncMock(return_value=mock_yahoo_standings_response)

        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_standings({"league_key": "461.l.61410"})

            assert "standings" in result
            assert len(result["standings"]) == 2
            assert result["standings"][0]["team"] == "Team Alpha"
            assert result["standings"][0]["rank"] == 1
            assert result["standings"][0]["wins"] == 10
            assert result["standings"][1]["team"] == "Team Bravo"
            assert result["standings"][1]["rank"] == 2
