"""Tests for cached Sleeper player experience enrichment."""

from __future__ import annotations

import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.services.sleeper_player_provider import SleeperPlayerProvider


class FakeTransport:
    def __init__(self, payload: dict | None = None, *, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        if self.error is not None:
            raise self.error
        return deepcopy(self.payload)


def sleeper_catalog() -> dict:
    return {
        "100": {
            "player_id": "100",
            "full_name": "Jordan Alpha",
            "position": "RB",
            "team": "SF",
            "years_exp": 2,
            "yahoo_id": 501,
        },
        "200": {
            "player_id": "200",
            "first_name": "Taylor",
            "last_name": "Beta",
            "position": "WR",
            "team": "JAC",
            "years_exp": 1,
            "yahoo_id": "502",
        },
        "300": {
            "player_id": "300",
            "full_name": "Duplicate Name",
            "position": "TE",
            "team": "DAL",
            "years_exp": 3,
        },
        "301": {
            "player_id": "301",
            "full_name": "Duplicate Name",
            "position": "TE",
            "team": "DAL",
            "years_exp": 4,
        },
        "400": {
            "player_id": "400",
            "full_name": "Ignore Quarterback",
            "position": "QB",
            "team": "SEA",
            "years_exp": 5,
        },
    }


@pytest.mark.asyncio
async def test_fetches_normalizes_resolves_and_persists_private_daily_cache(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / "private" / "sleeper-players.json"
    transport = FakeTransport(sleeper_catalog())
    provider = SleeperPlayerProvider(
        transport=transport,
        clock=lambda: now,
        cache_path=cache_path,
    )

    result = await provider.get_player_experience(
        [
            {
                "name": "Jordan Alpha",
                "position": "RB",
                "team": "SF",
                "player_key": "461.p.501",
            },
            {"name": "Taylor Beta", "position": "WR", "team": "JAX"},
            {"name": "Duplicate Name", "position": "TE", "team": "DAL"},
            {"name": "J. Alpha", "position": "RB", "team": "SF"},
        ]
    )

    assert result["status"] == "success"
    assert result["catalogPlayers"] == 4
    assert result["identityResolvedPlayers"] == 2
    assert result["players"][0]["experience_years"] == 2
    assert result["players"][1]["experience_years"] == 1
    assert result["players"][2]["identityResolved"] is False
    assert result["players"][3]["identityResolved"] is False
    assert transport.calls[0][1]["params"] == {"active": "true"}
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600

    no_network = FakeTransport(error=AssertionError("cache should prevent a request"))
    restarted = SleeperPlayerProvider(
        transport=no_network,
        clock=lambda: now + timedelta(hours=23),
        cache_path=cache_path,
    )
    cached = await restarted.get_player_experience(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    )

    assert no_network.calls == []
    assert cached["players"][0]["experience_years"] == 2
    assert cached["cacheStale"] is False


@pytest.mark.asyncio
async def test_uses_bounded_stale_cache_when_refresh_fails(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / "sleeper-players.json"
    initial = SleeperPlayerProvider(
        transport=FakeTransport(sleeper_catalog()),
        clock=lambda: now,
        cache_path=cache_path,
    )
    await initial.get_player_experience([])

    restarted = SleeperPlayerProvider(
        transport=FakeTransport(error=RuntimeError("private provider detail")),
        clock=lambda: now + timedelta(days=2),
        cache_path=cache_path,
    )
    result = await restarted.get_player_experience(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    )

    assert result["status"] == "degraded"
    assert result["cacheStale"] is True
    assert result["refreshFailed"] is True
    assert result["players"][0]["experience_years"] == 2
    assert "private provider detail" not in repr(result)


@pytest.mark.asyncio
async def test_fails_closed_after_cached_catalog_exceeds_freshness_bound(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / "sleeper-players.json"
    initial = SleeperPlayerProvider(
        transport=FakeTransport(sleeper_catalog()),
        clock=lambda: now,
        cache_path=cache_path,
    )
    await initial.get_player_experience([])

    restarted = SleeperPlayerProvider(
        transport=FakeTransport(error=RuntimeError("offline")),
        clock=lambda: now + timedelta(days=46),
        cache_path=cache_path,
    )
    result = await restarted.get_player_experience(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    )

    assert result["status"] == "unavailable"
    assert result["identityResolvedPlayers"] == 0
    assert result["players"][0]["experience_years"] is None
    assert result["warnings"] == ["Sleeper player experience is temporarily unavailable"]
