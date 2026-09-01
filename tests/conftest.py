"""Pytest configuration and shared fixtures for all tests."""

import json
import os
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
    test_env = {
        "YAHOO_ACCESS_TOKEN": "test_access_token_12345",
        "YAHOO_REFRESH_TOKEN": "test_refresh_token_67890",
        "YAHOO_CLIENT_ID": "test_client_id",
        "YAHOO_CLIENT_SECRET": "test_client_secret",
        "YAHOO_GUID": "TEST_GUID_12345",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    return test_env


@pytest.fixture
def mock_yahoo_league_response() -> Dict[str, Any]:
    """Mock Yahoo API response for leagues endpoint."""
    return {
        "fantasy_content": {
            "users": {
                "0": {
                    "user": [
                        [{"guid": "TEST_GUID_12345"}],
                        {
                            "games": {
                                "0": {
                                    "game": [
                                        [{"game_key": "461"}],
                                        {
                                            "leagues": {
                                                "0": {
                                                    "league": [
                                                        [
                                                            {
                                                                "league_key": "461.l.61410",
                                                                "league_id": "61410",
                                                                "name": "Anyone But Andy",
                                                                "season": "2025",
                                                                "num_teams": 10,
                                                                "current_week": 1,
                                                                "scoring_type": "head2head",
                                                                "is_finished": 0,
                                                            }
                                                        ]
                                                    ]
                                                },
                                                "count": 1,
                                            }
                                        },
                                    ]
                                }
                            }
                        },
                    ]
                }
            }
        }
    }


@pytest.fixture
def mock_yahoo_roster_response() -> Dict[str, Any]:
    """Mock Yahoo API response for team roster endpoint."""
    return {
        "fantasy_content": {
            "team": [
                [{"team_key": "461.l.61410.t.1"}],
                {
                    "roster": {
                        "0": {
                            "players": {
                                "0": {
                                    "player": [
                                        [
                                            {
                                                "player_key": "461.p.33536",
                                                "name": {"full": "Josh Allen"},
                                                "display_position": "QB",
                                                "editorial_team_abbr": "BUF",
                                                "status": "OK",
                                            }
                                        ],
                                        {"selected_position": [{"position": "QB"}]},
                                    ]
                                },
                                "1": {
                                    "player": [
                                        [
                                            {
                                                "player_key": "461.p.31860",
                                                "name": {"full": "Christian McCaffrey"},
                                                "display_position": "RB",
                                                "editorial_team_abbr": "SF",
                                                "status": "O",
                                            }
                                        ],
                                        {"selected_position": [{"position": "RB"}]},
                                    ]
                                },
                                "2": {
                                    "player": [
                                        [
                                            {
                                                "player_key": "461.p.30123",
                                                "name": {"full": "Cooper Kupp"},
                                                "display_position": "WR",
                                                "editorial_team_abbr": "LAR",
                                                "status": "OK",
                                            }
                                        ],
                                        {"selected_position": [{"position": "WR"}]},
                                    ]
                                },
                                "count": 3,
                            }
                        }
                    }
                },
            ]
        }
    }


@pytest.fixture
def mock_yahoo_free_agents_response() -> Dict[str, Any]:
    """Mock Yahoo API response for free agents/waiver wire endpoint."""
    return {
        "fantasy_content": {
            "league": [
                [{"league_key": "461.l.61410"}],
                {
                    "players": {
                        "0": {
                            "player": [
                                [
                                    {
                                        "player_key": "461.p.12345",
                                        "name": {"full": "Available Player"},
                                        "display_position": "RB",
                                        "editorial_team_abbr": "NYJ",
                                        "status": "OK",
                                    }
                                ],
                                {
                                    "ownership": {
                                        "ownership_percentage": 45,
                                        "weekly_change": 5,
                                    }
                                },
                            ]
                        },
                        "1": {
                            "player": [
                                [
                                    {
                                        "player_key": "461.p.67890",
                                        "name": {"full": "Injured Player"},
                                        "display_position": "WR",
                                        "editorial_team_abbr": "MIA",
                                        "status": "Q",
                                        "status_full": "Questionable",
                                    }
                                ],
                                {
                                    "ownership": {
                                        "ownership_percentage": 12,
                                        "weekly_change": -8,
                                    },
                                    "bye_weeks": {"week": "7"},
                                },
                            ]
                        },
                        "count": 2,
                    }
                },
            ]
        }
    }


@pytest.fixture
def mock_yahoo_standings_response() -> Dict[str, Any]:
    """Mock Yahoo API response for league standings."""
    return {
        "fantasy_content": {
            "league": [
                [{"league_key": "461.l.61410"}],
                {
                    "standings": [
                        {
                            "teams": {
                                "0": {
                                    "team": [
                                        [{"name": "Team Alpha"}],
                                        {
                                            "team_standings": {
                                                "rank": 1,
                                                "outcome_totals": {
                                                    "wins": 10,
                                                    "losses": 3,
                                                    "ties": 0,
                                                },
                                                "points_for": 1456.8,
                                                "points_against": 1234.5,
                                            }
                                        },
                                    ]
                                },
                                "1": {
                                    "team": [
                                        [{"name": "Team Bravo"}],
                                        {
                                            "team_standings": {
                                                "rank": 2,
                                                "outcome_totals": {
                                                    "wins": 9,
                                                    "losses": 4,
                                                    "ties": 0,
                                                },
                                                "points_for": 1389.2,
                                                "points_against": 1301.7,
                                            }
                                        },
                                    ]
                                },
                                "count": 2,
                            }
                        }
                    ]
                },
            ]
        }
    }


@pytest.fixture
def mock_rate_limiter():
    """Mock rate limiter for testing."""
    limiter = MagicMock()
    limiter.acquire = AsyncMock()
    limiter.get_status = MagicMock(
        return_value={
            "requests_remaining": 850,
            "requests_limit": 900,
            "window_reset_seconds": 1800,
        }
    )
    return limiter


@pytest.fixture
def mock_response_cache():
    """Mock response cache for testing."""
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    cache.clear = AsyncMock()
    cache.get_stats = MagicMock(
        return_value={
            "size": 15,
            "hits": 245,
            "misses": 87,
            "hit_rate": 0.738,
        }
    )
    return cache


@pytest.fixture
def sample_roster_data() -> list:
    """Sample parsed roster data for testing."""
    return [
        {
            "name": "Josh Allen",
            "position": "QB",
            "team": "BUF",
            "status": "OK",
        },
        {
            "name": "Christian McCaffrey",
            "position": "RB",
            "team": "SF",
            "status": "O",
        },
        {
            "name": "Cooper Kupp",
            "position": "WR",
            "team": "LAR",
            "status": "OK",
        },
        {
            "name": "Travis Kelce",
            "position": "TE",
            "team": "KC",
            "status": "OK",
        },
    ]


@pytest.fixture
def sample_sleeper_rankings() -> Dict[str, Any]:
    """Sample Sleeper API rankings data for testing."""
    return {
        "Josh Allen": {"rank": 1, "tier": "elite", "projection": 24.5},
        "Christian McCaffrey": {"rank": 2, "tier": "elite", "projection": 22.3},
        "Cooper Kupp": {"rank": 15, "tier": "solid", "projection": 14.8},
        "Travis Kelce": {"rank": 3, "tier": "elite", "projection": 12.1},
    }


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp session for API testing."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"test": "data"})
    mock_response.text = AsyncMock(return_value="test response")

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.post = MagicMock(return_value=mock_response)

    return mock_session
