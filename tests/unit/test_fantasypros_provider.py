"""Tests for bounded, privacy-safe FantasyPros news and injury enrichment."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.services.fantasypros_provider import FantasyProsProvider


class FakeTransport:
    def __init__(self, responses: dict[str, dict[str, Any] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "params": dict(params),
                "timeout_seconds": timeout_seconds,
                "max_body_bytes": max_body_bytes,
            }
        )
        endpoint = url.rsplit("/", 1)[-1]
        response_key = (
            f"{endpoint}:{params['player']}"
            if endpoint == "players" and "player" in params
            else endpoint
        )
        response = self.responses[response_key]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


@pytest.fixture
def source_payloads() -> dict[str, dict[str, Any]]:
    return {
        "players": {
            "sport": "NFL",
            "players": [
                {
                    "player_id": 101,
                    "player_name": "Jordan Alpha",
                    "position_id": "RB",
                    "team_id": "SF",
                    "filename": "must-not-escape.jpg",
                    "sportsdata_player_id": "must-not-escape",
                },
                {
                    "player_id": 202,
                    "player_name": "Case O'Neil",
                    "position_id": "WR",
                    "team_id": "NYJ",
                    "filename": "must-not-escape.jpg",
                },
            ],
        },
        "injuries": {
            "sport": "NFL",
            "injuries": [
                {
                    "player_id": 101,
                    "name": "Jordan Alpha",
                    "status": "Questionable",
                    "status_short": "Q",
                    "injury_update_date": "2026-08-31",
                    "comment": "raw medical commentary must not escape",
                    "yahoo_id": "private-unneeded-id",
                    "probability_of_playing": "0.51",
                }
            ],
        },
        "news": {
            "sport": "NFL",
            "items": [
                {
                    "id": 1,
                    "player_id": 101,
                    "team_id": "SF",
                    "title": "Jordan Alpha returns to limited work",
                    "created": "2026-09-01 12:00:00",
                    "categories": ["Injury", "Breaking"],
                    "link": "https://example.invalid/?secret=must-not-escape",
                    "desc": "raw description must not escape",
                    "impact": "raw impact must not escape",
                    "author": "must-not-escape",
                },
                {
                    "id": 2,
                    "player_id": 202,
                    "team_id": "NYJ",
                    "title": "Case O'Neil changes roles",
                    "created": "2026-08-01 12:00:00",
                    "categories": ["Transaction"],
                    "link": "https://example.invalid/old",
                    "desc": "stale description",
                    "impact": "stale impact",
                },
            ],
        },
    }


@pytest.mark.asyncio
async def test_provider_uses_official_contract_and_returns_only_allowlisted_fields(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    transport = FakeTransport(source_payloads)
    provider = FantasyProsProvider(
        api_key="unit-test-secret",
        transport=transport,
        clock=lambda: now,
    )

    result = await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "sf", "ignored": "field"}],
        year=2026,
        week=0,
    )

    assert result["status"] == "success"
    assert result["provider"] == "FantasyPros"
    assert result["retrievedAt"] == "2026-09-01T16:00:00Z"
    assert result["warnings"] == []
    assert len(transport.calls) == 3
    assert {call["url"] for call in transport.calls} == {
        "https://api.fantasypros.com/public/v2/json/nfl/players",
        "https://api.fantasypros.com/public/v2/json/nfl/injuries",
        "https://api.fantasypros.com/public/v2/json/nfl/news",
    }
    assert all(call["headers"] == {"x-api-key": "unit-test-secret"} for call in transport.calls)
    calls = {call["url"].rsplit("/", 1)[-1]: call for call in transport.calls}
    assert calls["players"]["params"] == {"ecr": "included"}
    assert calls["injuries"]["params"] == {
        "year": 2026,
        "week": 0,
        "include_probabilities": "true",
    }
    assert calls["news"]["params"] == {"limit": 100, "order_by": "updated"}

    player = result["players"][0]
    assert set(player) == {
        "name",
        "position",
        "team",
        "fantasypros_id",
        "identityResolved",
        "injury_status",
        "injury_source",
        "injury_updated_at",
        "injury_snapshot_at",
        "injury_fresh",
        "news_source",
        "news_updated_at",
        "news_fresh",
        "recentNews",
        "retrievedAt",
    }
    assert player == {
        "name": "Jordan Alpha",
        "position": "RB",
        "team": "SF",
        "fantasypros_id": 101,
        "identityResolved": True,
        "injury_status": "questionable",
        "injury_source": "FantasyPros",
        "injury_updated_at": "2026-08-31T00:00:00Z",
        "injury_snapshot_at": "2026-09-01T16:00:00Z",
        "injury_fresh": True,
        "news_source": "FantasyPros",
        "news_updated_at": "2026-09-01T12:00:00Z",
        "news_fresh": True,
        "recentNews": [
            {
                "headline": "Jordan Alpha returns to limited work",
                "category": "injury",
                "publishedAt": "2026-09-01T12:00:00Z",
            }
        ],
        "retrievedAt": "2026-09-01T16:00:00Z",
    }
    serialized = repr(result)
    for forbidden in (
        "unit-test-secret",
        "must-not-escape",
        "raw medical commentary",
        "raw description",
        "raw impact",
        "example.invalid",
        "probability_of_playing",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_provider_prefers_fp_id_and_otherwise_requires_exact_full_identity() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    transport = FakeTransport(
        {
            "players": {
                "players": [
                    {
                        "player_id": 10,
                        "player_name": "Amon-Ra St. Brown",
                        "position_id": "WR",
                        "team_id": "DET",
                    },
                    {
                        "player_id": 20,
                        "player_name": "Seattle Seahawks",
                        "position_id": "DST",
                        "team_id": "SEA",
                    },
                    {
                        "player_id": 30,
                        "player_name": "Alex Same",
                        "position_id": "RB",
                        "team_id": "ARI",
                    },
                    {
                        "player_id": 31,
                        "player_name": "Alex Same",
                        "position_id": "RB",
                        "team_id": "ARI",
                    },
                ]
            },
            "injuries": {
                "injuries": [
                    {
                        "player_id": 10,
                        "name": "Amon Ra St Brown",
                        "position_id": "WR",
                        "team_id": "DET",
                        "status": "",
                        "injury_update_date": "2026-09-01",
                    }
                ]
            },
            "news": {"items": []},
        }
    )
    provider = FantasyProsProvider(
        api_key="secret",
        transport=transport,
        clock=lambda: now,
    )

    result = await provider.get_player_updates(
        [
            {
                "fantasypros_id": 10,
                "name": "metadata can lag when the stable id is exact",
                "position": "RB",
                "team": "OLD",
            },
            {"name": "Amon Ra St Brown", "position": "WR", "team": "DET"},
            {"name": "A. St. Brown", "position": "WR", "team": "DET"},
            {"name": "Different DST label", "position": "DEF", "team": "SEA"},
            {"name": "Alex Same", "position": "RB", "team": "ARI"},
            {"name": "Amon-Ra St. Brown", "position": "WR", "team": "GB"},
        ],
        year=2026,
    )

    assert [player["fantasypros_id"] for player in result["players"]] == [
        10,
        10,
        None,
        20,
        None,
        None,
    ]
    assert [player["identityResolved"] for player in result["players"]] == [
        True,
        True,
        False,
        True,
        False,
        False,
    ]
    assert all(player["injury_status"] == "unknown" for player in result["players"])


@pytest.mark.asyncio
async def test_current_injury_snapshot_and_stale_news_are_distinguished(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 20, 16, tzinfo=timezone.utc)
    provider = FantasyProsProvider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: now,
    )

    result = await provider.get_player_updates(
        [
            {"name": "Jordan Alpha", "position": "RB", "team": "SF"},
            {"name": "Unknown Player", "position": "TE", "team": "FA"},
        ],
        year=2026,
    )

    current_injury, missing = result["players"]
    assert current_injury["injury_status"] == "questionable"
    assert current_injury["injury_source"] == "FantasyPros"
    assert current_injury["injury_updated_at"] == "2026-08-31T00:00:00Z"
    assert current_injury["injury_snapshot_at"] == "2026-09-20T16:00:00Z"
    assert current_injury["injury_fresh"] is True
    assert current_injury["news_source"] is None
    assert current_injury["news_updated_at"] == "2026-09-01T12:00:00Z"
    assert current_injury["news_fresh"] is False
    assert current_injury["recentNews"] == []
    assert missing["identityResolved"] is False
    assert missing["injury_status"] == "unknown"
    assert missing["injury_source"] is None
    assert missing["news_source"] is None


@pytest.mark.asyncio
async def test_provider_reads_env_without_returning_key_and_degrades_safely(
    monkeypatch: pytest.MonkeyPatch,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    secret = "env-secret-that-must-not-leak"
    monkeypatch.setenv("FANTASY_PROS_API", secret)
    source_payloads["news"] = RuntimeError(f"failed with header x-api-key={secret}")
    provider = FantasyProsProvider(
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    result = await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert result["status"] == "degraded"
    assert result["players"][0]["news_source"] is None
    assert result["warnings"] == ["FantasyPros news is temporarily unavailable"]
    assert secret not in repr(result)
    assert secret not in repr(provider)


@pytest.mark.asyncio
async def test_missing_api_key_returns_unknown_without_network(
    monkeypatch: pytest.MonkeyPatch,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    monkeypatch.delenv("FANTASY_PROS_API", raising=False)
    transport = FakeTransport(source_payloads)
    provider = FantasyProsProvider(
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    result = await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert result["status"] == "unavailable"
    assert result["warnings"] == [
        "FantasyPros enrichment is unavailable because FANTASY_PROS_API is not configured"
    ]
    assert result["players"][0]["injury_status"] == "unknown"
    assert result["players"][0]["identityResolved"] is False
    assert transport.calls == []


@pytest.mark.asyncio
async def test_provider_caches_fast_data_and_player_directory_on_separate_ttls(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, tzinfo=timezone.utc)]
    transport = FakeTransport(source_payloads)
    provider = FantasyProsProvider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
        cache_ttl_seconds=300,
        player_cache_ttl_seconds=86_400,
    )
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]

    await provider.get_player_updates(identity, year=2026)
    await provider.get_player_updates(identity, year=2026)
    assert len(transport.calls) == 3

    current[0] += timedelta(minutes=6)
    await provider.get_player_updates(identity, year=2026)
    assert [call["url"].rsplit("/", 1)[-1] for call in transport.calls].count("players") == 1
    assert len(transport.calls) == 5

    current[0] += timedelta(days=1)
    await provider.get_player_updates(identity, year=2026)
    assert [call["url"].rsplit("/", 1)[-1] for call in transport.calls].count("players") == 2
    assert len(transport.calls) == 8


@pytest.mark.asyncio
async def test_provider_cache_is_lru_bounded_across_rotating_targeted_ids() -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    generation = [0]

    class RotatingTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None]] = []

        async def get_json(self, url, *, headers, params, **_arguments):
            endpoint = url.rsplit("/", 1)[-1]
            player_id = params.get("player") if endpoint == "players" else None
            self.calls.append((endpoint, player_id if isinstance(player_id, int) else None))
            if endpoint == "players" and player_id is not None:
                return {
                    "sport": "NFL",
                    "players": [
                        {
                            "player_id": player_id,
                            "player_name": f"Player {player_id}",
                            "position_id": "WR",
                            "team_id": "SF",
                        }
                    ],
                }
            if endpoint == "players":
                return {"sport": "NFL", "players": []}
            if endpoint == "injuries":
                return {"sport": "NFL", "injuries": []}
            first_id = 1_000 + generation[0] * 10
            return {
                "sport": "NFL",
                "items": [
                    {
                        "player_id": player_id,
                        "title": f"Player {player_id} role update",
                        "created": current[0].strftime("%Y-%m-%d %H:%M:%S"),
                        "categories": ["Transaction"],
                    }
                    for player_id in range(first_id, first_id + 10)
                ],
            }

    transport = RotatingTransport()
    provider = FantasyProsProvider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
        cache_ttl_seconds=1,
        player_cache_ttl_seconds=86_400,
        max_cache_entries=16,
    )

    for batch in range(3):
        generation[0] = batch
        first_id = 1_000 + batch * 10
        candidates = [
            {"name": f"Player {player_id}", "position": "WR", "team": "SF"}
            for player_id in range(first_id, first_id + 10)
        ]
        await provider.get_player_updates(candidates, year=2026)
        current[0] += timedelta(seconds=2)

    assert len(provider._cache) <= 16
    initial_first_id_calls = transport.calls.count(("players", 1_000))
    generation[0] = 0
    await provider.get_player_updates(
        [
            {"name": f"Player {player_id}", "position": "WR", "team": "SF"}
            for player_id in range(1_000, 1_010)
        ],
        year=2026,
    )
    assert transport.calls.count(("players", 1_000)) == initial_first_id_calls + 1
    assert len(provider._cache) <= 16


@pytest.mark.asyncio
async def test_provider_clamps_request_and_output_bounds() -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    transport = FakeTransport(
        {
            "players": {
                "players": [
                    {
                        "player_id": player_id,
                        "player_name": f"Player {player_id}",
                        "position_id": "RB",
                        "team_id": "SF",
                    }
                    for player_id in range(1, 5)
                ]
            },
            "injuries": {
                "injuries": [
                    {
                        "player_id": player_id,
                        "status": "OUT",
                        "injury_update_date": "2026-09-01",
                    }
                    for player_id in range(1, 5)
                ]
            },
            "news": {
                "items": [
                    {
                        "player_id": 1,
                        "title": "X" * 500,
                        "created": f"2026-09-01 12:{index % 60:02d}:00",
                        "categories": ["Breaking", "Arbitrary"],
                    }
                    for index in range(105)
                ]
            },
        }
    )
    provider = FantasyProsProvider(
        api_key="secret",
        transport=transport,
        clock=lambda: now,
        timeout_seconds=999,
        max_body_bytes=999_999_999,
        max_players=2,
        max_injuries=1,
        news_limit=1_000,
        recent_news_limit=2,
    )

    result = await provider.get_player_updates(
        [
            {"name": "Player 1", "position": "RB", "team": "SF"},
            {"name": "Player 3", "position": "RB", "team": "SF"},
        ],
        year=2026,
        week=999,
    )

    assert all(call["timeout_seconds"] == 10.0 for call in transport.calls)
    assert all(call["max_body_bytes"] == 4_000_000 for call in transport.calls)
    calls = {call["url"].rsplit("/", 1)[-1]: call for call in transport.calls}
    assert calls["news"]["params"]["limit"] == 100
    assert calls["injuries"]["params"]["week"] == 25
    assert len(result["players"][0]["recentNews"]) == 2
    assert len(result["players"][0]["recentNews"][0]["headline"]) == 240
    assert result["players"][1]["identityResolved"] is False
    assert result["warnings"] == [
        "FantasyPros player catalog exceeded the bounded record limit",
        "FantasyPros injuries exceeded the bounded record limit",
        "FantasyPros news exceeded the bounded record limit",
    ]


@pytest.mark.asyncio
async def test_limited_catalog_resolves_current_injury_from_exact_row_identity() -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    transport = FakeTransport(
        {
            "players": {
                "sport": "NFL",
                "count": 502,
                "limit": 10,
                "public_api_limited": True,
                "players": [
                    {
                        "player_id": 900,
                        "player_name": "Unrelated Defense",
                        "position_id": "DST",
                        "team_id": "SEA",
                    }
                ],
            },
            "injuries": {
                "sport": "NFL",
                "count": 1,
                "injuries": [
                    {
                        "player_id": 101,
                        "name": "Jordan Alpha",
                        "position_id": "RB",
                        "team_id": "SF",
                        "status": "OUT",
                        "status_short": "O",
                        "injury_update_date": "2026-03-01",
                        "comment": "must not escape",
                    }
                ],
            },
            "news": {"sport": "NFL", "count": 0, "items": []},
        }
    )
    provider = FantasyProsProvider(api_key="secret", transport=transport, clock=lambda: now)

    result = await provider.get_player_updates(
        [
            {"name": "Jordan Alpha", "position": "RB", "team": "SF"},
            {"name": "Jordan Alpha", "position": "RB", "team": "SEA"},
        ],
        year=2026,
    )

    assert result["status"] == "degraded"
    assert result["warnings"] == [
        "FantasyPros player catalog coverage is limited by the public API"
    ]
    assert result["coverage"]["playerCatalog"] == {
        "fetchedAt": "2026-09-01T16:00:00Z",
        "returned": 1,
        "reportedCount": 502,
        "reportedLimit": 10,
        "publicApiLimited": True,
    }
    exact, wrong_team = result["players"]
    assert exact["identityResolved"] is True
    assert exact["fantasypros_id"] == 101
    assert exact["injury_status"] == "out"
    assert exact["injury_updated_at"] == "2026-03-01T00:00:00Z"
    assert exact["injury_snapshot_at"] == "2026-09-01T16:00:00Z"
    assert exact["injury_fresh"] is True
    assert wrong_team["identityResolved"] is False
    assert wrong_team["injury_status"] == "unknown"
    assert "must not escape" not in repr(result)


@pytest.mark.asyncio
async def test_limited_catalog_uses_ten_cached_targeted_news_identity_lookups() -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    news_items = [
        {
            "player_id": player_id,
            "title": f"Player {player_id} role update",
            "created": "2026-09-01 12:00:00",
            "categories": ["Transaction", "News"],
            "link": f"https://example.invalid/{player_id}?token=must-not-escape",
        }
        for player_id in range(100, 112)
    ]
    responses: dict[str, dict[str, Any] | Exception] = {
        "players": {
            "sport": "NFL",
            "count": 502,
            "limit": 10,
            "public_api_limited": True,
            "players": [],
        },
        "injuries": {"sport": "NFL", "count": 0, "injuries": []},
        "news": {
            "sport": "NFL",
            "count": len(news_items),
            "limit": 10,
            "public_api_limited": True,
            "items": news_items,
        },
    }
    for player_id in range(100, 110):
        responses[f"players:{player_id}"] = {
            "sport": "NFL",
            "count": 1,
            "limit": 10,
            "public_api_limited": True,
            "players": [
                {
                    "player_id": player_id,
                    "player_name": f"Player {player_id}",
                    "position_id": "WR",
                    "team_id": "SF",
                    "filename": "must-not-escape",
                }
            ],
        }
    transport = FakeTransport(responses)
    provider = FantasyProsProvider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
        cache_ttl_seconds=300,
        player_cache_ttl_seconds=86_400,
    )
    candidates = [
        {"name": f"Player {player_id}", "position": "WR", "team": "SF"}
        for player_id in range(100, 112)
    ]

    first = await provider.get_player_updates(candidates, year=2026)

    targeted_calls = [
        call
        for call in transport.calls
        if call["url"].endswith("/players") and "player" in call["params"]
    ]
    assert [call["params"]["player"] for call in targeted_calls] == list(range(100, 110))
    assert first["status"] == "degraded"
    assert first["coverage"]["targetedPlayerLookups"] == {
        "attempted": 10,
        "resolved": 10,
        "capped": True,
    }
    assert any("player catalog coverage" in warning for warning in first["warnings"])
    assert any("news coverage" in warning for warning in first["warnings"])
    assert any("news identity coverage" in warning for warning in first["warnings"])
    assert all(player["identityResolved"] is True for player in first["players"][:10])
    assert all(player["news_fresh"] is True for player in first["players"][:10])
    assert all(player["identityResolved"] is False for player in first["players"][10:])
    assert all(player["news_fresh"] is False for player in first["players"][10:])
    assert "example.invalid" not in repr(first)
    assert "must-not-escape" not in repr(first)

    call_count = len(transport.calls)
    await provider.get_player_updates(candidates, year=2026)
    assert len(transport.calls) == call_count

    current[0] += timedelta(hours=6)
    await provider.get_player_updates(candidates, year=2026)
    new_calls = transport.calls[call_count:]
    assert {call["url"].rsplit("/", 1)[-1] for call in new_calls} == {"injuries", "news"}


@pytest.mark.asyncio
async def test_targeted_news_identity_failure_is_generic_and_remains_unknown() -> None:
    secret = "targeted-secret-must-not-escape"
    transport = FakeTransport(
        {
            "players": {"sport": "NFL", "count": 0, "players": []},
            "injuries": {"sport": "NFL", "count": 0, "injuries": []},
            "news": {
                "sport": "NFL",
                "count": 1,
                "items": [
                    {
                        "player_id": 101,
                        "title": "Jordan Alpha changes roles",
                        "created": "2026-09-01 12:00:00",
                        "categories": ["Transaction"],
                    }
                ],
            },
            "players:101": RuntimeError(f"failed with x-api-key={secret}"),
        }
    )
    provider = FantasyProsProvider(
        api_key=secret,
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
    )

    result = await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert result["status"] == "degraded"
    assert result["warnings"] == [
        "FantasyPros recent-news player identity is temporarily unavailable"
    ]
    assert result["coverage"]["targetedPlayerLookups"] == {
        "attempted": 1,
        "resolved": 0,
        "capped": False,
    }
    assert result["players"][0]["identityResolved"] is False
    assert result["players"][0]["news_fresh"] is False
    assert secret not in repr(result)
