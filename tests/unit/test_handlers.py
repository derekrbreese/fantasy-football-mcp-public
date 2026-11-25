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


class TestLeagueHandlers:
    """Test league-level handlers."""

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


def _scoreboard_team(key, name, points, projected, win_probability):
    return {
        "team": [
            [{"team_key": key}, {"team_id": key.split(".")[-1]}, {"name": name}, []],
            {
                "win_probability": win_probability,
                "team_points": {"coverage_type": "week", "week": "12", "total": points},
                "team_projected_points": {"coverage_type": "week", "week": "12", "total": projected},
            },
        ]
    }


def _scoreboard_response():
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "461.l.61410", "name": "Test League"},
                {
                    "scoreboard": {
                        "week": "12",
                        "0": {
                            "matchups": {
                                "0": {
                                    "matchup": {
                                        "week": "12",
                                        "status": "postevent",
                                        "is_playoffs": "0",
                                        "is_consolation": "0",
                                        "is_tied": 0,
                                        "winner_team_key": "461.l.61410.t.1",
                                        "0": {
                                            "teams": {
                                                "0": _scoreboard_team(
                                                    "461.l.61410.t.1", "Team Alpha", "121.44", "110.10", 1
                                                ),
                                                "1": _scoreboard_team(
                                                    "461.l.61410.t.2", "Team Bravo", "98.70", "115.32", 0
                                                ),
                                                "count": 2,
                                            }
                                        },
                                    }
                                },
                                "1": {
                                    "matchup": {
                                        "week": "12",
                                        "status": "midevent",
                                        "is_playoffs": "1",
                                        "is_consolation": "0",
                                        "0": {
                                            "teams": {
                                                "0": _scoreboard_team(
                                                    "461.l.61410.t.3", "Team Charlie", "60.00", "101.00", 0.41
                                                ),
                                                "1": _scoreboard_team(
                                                    "461.l.61410.t.4", "Team Delta", "72.50", "104.20", 0.59
                                                ),
                                                "count": 2,
                                            }
                                        },
                                    }
                                },
                                "count": 2,
                            }
                        },
                    }
                },
            ]
        }
    }


def _transaction_player(key, name, position, nfl_team, transaction_data):
    return {
        "player": [
            [
                {"player_key": key},
                {"name": {"full": name, "first": name.split()[0]}},
                {"editorial_team_abbr": nfl_team},
                {"display_position": position},
            ],
            {"transaction_data": transaction_data},
        ]
    }


def _transactions_response():
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "461.l.61410"},
                {
                    "transactions": {
                        "0": {
                            "transaction": [
                                {
                                    "transaction_key": "461.l.61410.tr.30",
                                    "type": "add/drop",
                                    "status": "successful",
                                    "timestamp": "1700000000",
                                    "faab_bid": "17",
                                },
                                {
                                    "players": {
                                        "0": _transaction_player(
                                            "461.p.1",
                                            "Rookie Back",
                                            "RB",
                                            "KC",
                                            [
                                                {
                                                    "type": "add",
                                                    "source_type": "waivers",
                                                    "destination_type": "team",
                                                    "destination_team_key": "461.l.61410.t.1",
                                                    "destination_team_name": "Team Alpha",
                                                }
                                            ],
                                        ),
                                        "1": _transaction_player(
                                            "461.p.2",
                                            "Old Kicker",
                                            "K",
                                            "DAL",
                                            {
                                                "type": "drop",
                                                "source_type": "team",
                                                "source_team_key": "461.l.61410.t.1",
                                                "source_team_name": "Team Alpha",
                                                "destination_type": "waivers",
                                            },
                                        ),
                                        "count": 2,
                                    }
                                },
                            ]
                        },
                        "1": {
                            "transaction": [
                                {
                                    "transaction_key": "461.l.61410.tr.29",
                                    "type": "trade",
                                    "status": "successful",
                                    "timestamp": "1699900000",
                                    "trader_team_key": "461.l.61410.t.2",
                                    "trader_team_name": "Team Bravo",
                                    "tradee_team_key": "461.l.61410.t.3",
                                    "tradee_team_name": "Team Charlie",
                                },
                                {
                                    "players": {
                                        "0": _transaction_player(
                                            "461.p.3",
                                            "Star Receiver",
                                            "WR",
                                            "MIA",
                                            [
                                                {
                                                    "type": "trade",
                                                    "source_type": "team",
                                                    "source_team_key": "461.l.61410.t.2",
                                                    "source_team_name": "Team Bravo",
                                                    "destination_type": "team",
                                                    "destination_team_key": "461.l.61410.t.3",
                                                    "destination_team_name": "Team Charlie",
                                                }
                                            ],
                                        ),
                                        "count": 1,
                                    }
                                },
                            ]
                        },
                        "count": 2,
                    }
                },
            ]
        }
    }


class TestScoreboardHandler:
    """Test the league-wide scoreboard handler."""

    async def test_requires_league_key(self):
        from src.handlers.league_handlers import handle_ff_get_scoreboard

        result = await handle_ff_get_scoreboard({})
        assert result == {"error": "league_key is required"}

    async def test_parses_all_matchups(self):
        from src.handlers.league_handlers import handle_ff_get_scoreboard

        mock_api_call = AsyncMock(return_value=_scoreboard_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_scoreboard({"league_key": "461.l.61410"})

        mock_api_call.assert_awaited_once_with("league/461.l.61410/scoreboard")
        assert result["week"] == 12
        assert result["total_matchups"] == 2

        final, live = result["matchups"]
        assert final["status"] == "postevent"
        assert final["winner_team_key"] == "461.l.61410.t.1"
        assert final["is_playoffs"] is False
        assert final["teams"][0] == {
            "team_key": "461.l.61410.t.1",
            "name": "Team Alpha",
            "points": 121.44,
            "projected_points": 110.10,
            "win_probability": 1.0,
        }
        assert final["teams"][1]["name"] == "Team Bravo"
        assert final["teams"][1]["points"] == 98.70

        assert live["is_playoffs"] is True
        assert live["winner_team_key"] is None
        assert [team["win_probability"] for team in live["teams"]] == [0.41, 0.59]

    async def test_teams_directly_under_matchup(self):
        """Yahoo sometimes nests teams under matchup["teams"] without the "0" wrapper."""
        from src.handlers.league_handlers import handle_ff_get_scoreboard

        payload = _scoreboard_response()
        matchups = payload["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
        for key in ("0", "1"):
            matchup = matchups[key]["matchup"]
            matchup["teams"] = matchup.pop("0")["teams"]

        mock_api_call = AsyncMock(return_value=payload)
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_scoreboard({"league_key": "461.l.61410"})

        assert result["total_matchups"] == 2
        assert [team["name"] for team in result["matchups"][0]["teams"]] == [
            "Team Alpha",
            "Team Bravo",
        ]

    async def test_week_is_passed_to_yahoo(self):
        from src.handlers.league_handlers import handle_ff_get_scoreboard

        mock_api_call = AsyncMock(return_value=_scoreboard_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            await handle_ff_get_scoreboard({"league_key": "461.l.61410", "week": 9})

        mock_api_call.assert_awaited_once_with("league/461.l.61410/scoreboard;week=9")

    async def test_empty_response_returns_no_matchups(self):
        from src.handlers.league_handlers import handle_ff_get_scoreboard

        mock_api_call = AsyncMock(return_value={"fantasy_content": {"league": [{}]}})
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_scoreboard({"league_key": "461.l.61410", "week": 3})

        assert result["matchups"] == []
        assert result["total_matchups"] == 0
        assert result["week"] == 3


class TestTransactionsHandler:
    """Test the league transactions handler."""

    async def test_requires_league_key(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        result = await handle_ff_get_transactions({})
        assert result == {"error": "league_key is required"}

    async def test_rejects_unknown_type(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        mock_api_call = AsyncMock()
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_transactions(
                {"league_key": "461.l.61410", "types": ["add", "waiver"]}
            )

        assert "types must be drawn from" in result["error"]
        mock_api_call.assert_not_awaited()

    async def test_parses_add_drop_and_trade(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        mock_api_call = AsyncMock(return_value=_transactions_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_transactions({"league_key": "461.l.61410"})

        mock_api_call.assert_awaited_once_with("league/461.l.61410/transactions;count=25")
        assert result["types"] == "all"
        assert result["total_transactions"] == 2

        add_drop, trade = result["transactions"]
        assert add_drop["type"] == "add/drop"
        assert add_drop["faab_bid"] == 17
        assert add_drop["timestamp"] == "2023-11-14T22:13:20+00:00"
        added, dropped = add_drop["players"]
        assert added["name"] == "Rookie Back"
        assert added["position"] == "RB"
        assert added["nfl_team"] == "KC"
        assert added["action"] == "add"
        assert added["source_type"] == "waivers"
        assert added["destination_team"] == "Team Alpha"
        assert dropped["action"] == "drop"
        assert dropped["source_team"] == "Team Alpha"
        assert dropped["destination_type"] == "waivers"

        assert trade["type"] == "trade"
        assert trade["faab_bid"] is None
        assert trade["trader_team"] == "Team Bravo"
        assert trade["tradee_team"] == "Team Charlie"
        assert trade["players"][0]["source_team"] == "Team Bravo"
        assert trade["players"][0]["destination_team"] == "Team Charlie"
        assert "trader_team" not in add_drop

    async def test_types_and_count_build_endpoint(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        mock_api_call = AsyncMock(return_value=_transactions_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_transactions({"league_key": "461.l.61410", "count": 500})

        mock_api_call.assert_awaited_once_with("league/461.l.61410/transactions;count=100")
        assert result["types"] == "all"

    async def test_type_filter_asks_yahoo_once_per_family(self):
        """Yahoo ignores comma lists and treats add/drop as one filter, so filter here."""
        from src.handlers.league_handlers import handle_ff_get_transactions

        mock_api_call = AsyncMock(return_value=_transactions_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_transactions(
                {"league_key": "461.l.61410", "types": "add, trade,add", "count": 500}
            )

        assert [call.args[0] for call in mock_api_call.await_args_list] == [
            "league/461.l.61410/transactions;types=add;count=100",
            "league/461.l.61410/transactions;types=trade;count=100",
        ]
        assert result["types"] == ["add", "trade"]
        # Both requests return the same two rows; each is kept once, newest first.
        assert [row["type"] for row in result["transactions"]] == ["add/drop", "trade"]

    async def test_type_filter_is_exact(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        mock_api_call = AsyncMock(return_value=_transactions_response())
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            trades = await handle_ff_get_transactions(
                {"league_key": "461.l.61410", "types": ["trade"]}
            )
            drops = await handle_ff_get_transactions(
                {"league_key": "461.l.61410", "types": ["drop"], "count": 1}
            )

        assert [row["type"] for row in trades["transactions"]] == ["trade"]
        # A swap counts as a drop; the trade Yahoo sent alongside it does not.
        assert [row["type"] for row in drops["transactions"]] == ["add/drop"]
        assert mock_api_call.await_args_list[-1].args[0].endswith(";types=add;count=100")

    async def test_no_transactions(self):
        from src.handlers.league_handlers import handle_ff_get_transactions

        payload = {"fantasy_content": {"league": [{}, {"transactions": []}]}}
        mock_api_call = AsyncMock(return_value=payload)
        with patch("src.handlers.league_handlers.yahoo_api_call", mock_api_call):
            result = await handle_ff_get_transactions({"league_key": "461.l.61410"})

        assert result["transactions"] == []
        assert result["total_transactions"] == 0


def _standings_team(name, rank, wins, losses, points_for, points_against):
    return {
        "team": [
            [{"name": name}],
            {
                "team_standings": {
                    "rank": rank,
                    "outcome_totals": {"wins": wins, "losses": losses, "ties": 0},
                    "points_for": points_for,
                    "points_against": points_against,
                }
            },
        ]
    }


class TestStandingsOrdering:
    """Yahoo returns ranks as strings; they must sort numerically."""

    async def test_string_ranks_sort_numerically(self):
        from src.handlers.league_handlers import handle_ff_get_standings

        teams = {
            "0": _standings_team("First", "1", "8", "2", "1574.02", 1265.1799999999998),
            "1": _standings_team("Tenth", "10", "3", "7", "1068.36", 1345.8),
            "2": _standings_team("Second", "2", "7", "3", "1585.96", 1297.0600000000002),
            "3": _standings_team("Unranked", "", "0", "0", "0", 0),
            "count": 4,
        }
        payload = {"fantasy_content": {"league": [{}, {"standings": [{"teams": teams}]}]}}

        with patch("src.handlers.league_handlers.yahoo_api_call", AsyncMock(return_value=payload)):
            result = await handle_ff_get_standings({"league_key": "461.l.61410"})

        rows = result["standings"]
        assert [row["team"] for row in rows] == ["First", "Second", "Tenth", "Unranked"]
        assert [row["rank"] for row in rows] == [1, 2, 10, 0]
        assert rows[0]["wins"] == 8 and rows[0]["losses"] == 2
        assert rows[0]["points_for"] == 1574.02
        assert rows[0]["points_against"] == 1265.18


def _team_matchup(week, status, opponent):
    return {
        "matchup": {
            "0": {
                "teams": {
                    "0": {"team": [[{"team_key": "470.l.1.t.4"}, {"name": "Mine"}], {}]},
                    "1": {"team": [[{"team_key": opponent}, {"name": opponent}], {}]},
                    "count": 2,
                }
            },
            "week": week,
            "status": status,
        }
    }


def _team_matchups_response(statuses):
    """Season of matchups; week 1 is a string and later weeks ints, as Yahoo returns them."""
    matchups = {
        str(i): _team_matchup("1" if i == 0 else i + 1, status, f"470.l.1.t.{i + 5}")
        for i, status in enumerate(statuses)
    }
    matchups["count"] = len(statuses)
    return {
        "fantasy_content": {
            "team": [[{"team_key": "470.l.1.t.4"}], {"matchups": matchups}],
            "time": "1ms",
        }
    }


class TestMatchupWeekSelection:
    """ff_get_matchup must return one week, not the whole season."""

    @staticmethod
    async def _call(payload, arguments):
        import src.handlers.matchup_handlers as matchup_mod

        api = AsyncMock(return_value=payload)
        with (
            patch.object(matchup_mod, "get_user_team_key", AsyncMock(return_value="470.l.1.t.4")),
            patch.object(matchup_mod, "yahoo_api_call", api),
        ):
            result = await matchup_mod.handle_ff_get_matchup(arguments)
        return result, api

    @staticmethod
    def _weeks(result):
        matchups = result["raw_matchups"]["fantasy_content"]["team"][1]["matchups"]
        return matchups["count"], [
            int(entry["matchup"]["week"]) for key, entry in matchups.items() if key != "count"
        ]

    async def test_defaults_to_current_week(self):
        payload = _team_matchups_response(["postevent", "postevent", "midevent", "preevent"])
        result, api = await self._call(payload, {"league_key": "470.l.1"})

        api.assert_awaited_once_with("team/470.l.1.t.4/matchups")
        assert result["week"] == 3
        assert self._weeks(result) == (1, [3])

    async def test_between_weeks_picks_next_unplayed(self):
        payload = _team_matchups_response(["postevent", "postevent", "preevent", "preevent"])
        result, _ = await self._call(payload, {"league_key": "470.l.1"})
        assert result["week"] == 3

    async def test_finished_season_returns_last_week(self):
        payload = _team_matchups_response(["postevent", "postevent", "postevent"])
        result, _ = await self._call(payload, {"league_key": "470.l.1"})
        assert result["week"] == 3
        assert self._weeks(result) == (1, [3])

    async def test_requested_week_including_string_week(self):
        payload = _team_matchups_response(["postevent", "postevent", "midevent"])
        result, _ = await self._call(payload, {"league_key": "470.l.1", "week": 1})
        assert result["week"] == 1
        assert self._weeks(result) == (1, [1])

    async def test_unknown_week_returns_no_matchups(self):
        payload = _team_matchups_response(["postevent", "midevent"])
        result, _ = await self._call(payload, {"league_key": "470.l.1", "week": 9})
        assert result["week"] == 9
        assert self._weeks(result) == (0, [])

    async def test_cached_response_is_not_mutated(self):
        payload = _team_matchups_response(["postevent", "midevent", "preevent"])
        await self._call(payload, {"league_key": "470.l.1"})
        assert payload["fantasy_content"]["team"][1]["matchups"]["count"] == 3
        assert payload["fantasy_content"]["time"] == "1ms"

    async def test_unexpected_shape_passes_through(self):
        payload = {"fantasy_content": {"team": "unexpected"}}
        result, _ = await self._call(payload, {"league_key": "470.l.1", "week": 2})
        assert result["raw_matchups"] == payload
        assert result["week"] == 2
