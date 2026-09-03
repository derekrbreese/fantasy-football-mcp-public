"""Tests for cached Sleeper player experience enrichment."""

from __future__ import annotations

import json
import sqlite3
import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import cache_sleeper_players
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
    cache_path = tmp_path / "private" / "provider-snapshots.sqlite3"
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
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute(
            "SELECT endpoint, variant FROM snapshots"
        ).fetchall() == [("sleeper_players", "active")]

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
async def test_cache_warmer_respects_ttl_and_supports_explicit_refresh(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / "provider-snapshots.sqlite3"
    transport = FakeTransport(sleeper_catalog())
    provider = SleeperPlayerProvider(
        transport=transport,
        clock=lambda: now,
        cache_path=cache_path,
    )

    first = await provider.warm_player_cache()
    cached = await provider.warm_player_cache()
    forced = await provider.warm_player_cache(force_refresh=True)

    assert first == cached == forced
    assert first == {
        "status": "success",
        "provider": "Sleeper",
        "catalogFetchedAt": "2026-09-03T18:00:00Z",
        "cacheStale": False,
        "refreshFailed": False,
        "catalogPlayers": 4,
    }
    assert len(transport.calls) == 2


def test_cache_warmer_cli_prints_only_bounded_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeProvider:
        async def warm_player_cache(self, *, force_refresh=False):
            assert force_refresh is True
            return {
                "status": "success",
                "provider": "Sleeper",
                "catalogFetchedAt": "2026-09-03T18:00:00Z",
                "cacheStale": False,
                "refreshFailed": False,
                "catalogPlayers": 1234,
                "players": [{"name": "must not escape"}],
                "private": "must not escape",
            }

    monkeypatch.setattr(cache_sleeper_players, "SleeperPlayerProvider", FakeProvider)

    assert cache_sleeper_players.main(["--force"]) == 0
    output = capsys.readouterr().out
    assert '"catalogPlayers":1234' in output
    assert "must not escape" not in output


@pytest.mark.asyncio
async def test_uses_bounded_stale_cache_when_refresh_fails(tmp_path: Path) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / "provider-snapshots.sqlite3"
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
    cache_path = tmp_path / "provider-snapshots.sqlite3"
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


@pytest.mark.asyncio
async def test_migrates_legacy_json_cache_into_shared_database(tmp_path: Path) -> None:
    fetched_at = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    legacy_path = tmp_path / "sleeper-players.json"
    database_path = tmp_path / "provider-snapshots.sqlite3"
    legacy_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "fetchedAt": "2026-09-03T18:00:00Z",
                "players": [
                    {
                        "sleeperId": "100",
                        "name": "Jordan Alpha",
                        "position": "RB",
                        "team": "SF",
                        "yearsExperience": 2,
                        "yahooId": "501",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    legacy_path.chmod(0o600)
    transport = FakeTransport(error=AssertionError("migration should prevent a request"))
    provider = SleeperPlayerProvider(
        transport=transport,
        clock=lambda: fetched_at + timedelta(hours=1),
        cache_path=database_path,
        legacy_json_cache_path=legacy_path,
    )

    result = await provider.get_player_experience(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    )

    assert transport.calls == []
    assert result["status"] == "success"
    assert result["players"][0]["experience_years"] == 2
    assert database_path.is_file()
    assert not legacy_path.exists()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT endpoint, returned_count FROM snapshots"
        ).fetchall() == [("sleeper_players", 1)]
