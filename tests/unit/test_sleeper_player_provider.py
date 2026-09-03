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
from src.services import sleeper_player_provider as sleeper_provider_module
from src.services.sleeper_player_provider import (
    AiohttpSleeperTransport,
    SleeperPlayerProvider,
)

_OLD_BODY_LIMIT = 10_000_000
_EXPECTED_BODY_LIMIT = 16 * 1024 * 1024


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


class FakeResponseContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class FakeHttpResponse:
    def __init__(self, body: bytes, *, content_length: int | None = None) -> None:
        self.status = 200
        self.content_length = len(body) if content_length is None else content_length
        self.content = FakeResponseContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None


class FakeClientSession:
    def __init__(self, response: FakeHttpResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def get(self, _url, **_kwargs):
        return self.response


def install_http_response(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeHttpResponse,
) -> None:
    monkeypatch.setattr(
        sleeper_provider_module.aiohttp,
        "ClientSession",
        lambda **_kwargs: FakeClientSession(response),
    )


@pytest.mark.asyncio
async def test_transport_accepts_catalog_over_old_body_limit_and_normalizes_before_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_private_padding = "x" * _OLD_BODY_LIMIT
    body = json.dumps(
        {
            "100": {
                "player_id": "100",
                "full_name": "Jordan Alpha",
                "position": "RB",
                "team": "SF",
                "years_exp": 2,
                "private_padding": raw_private_padding,
            }
        },
        separators=(",", ":"),
    ).encode()
    assert _OLD_BODY_LIMIT < len(body) < _EXPECTED_BODY_LIMIT
    install_http_response(monkeypatch, FakeHttpResponse(body))
    cache_path = tmp_path / "provider-snapshots.sqlite3"
    provider = SleeperPlayerProvider(
        transport=AiohttpSleeperTransport(),
        clock=lambda: datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc),
        cache_path=cache_path,
    )

    result = await provider.get_player_experience(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    )

    assert result["status"] == "success"
    assert result["catalogPlayers"] == 1
    assert result["players"][0]["experience_years"] == 2
    with sqlite3.connect(cache_path) as connection:
        records_json, record_limit = connection.execute(
            "SELECT records_json, record_limit FROM snapshots"
        ).fetchone()
    assert "private_padding" not in records_json
    assert len(records_json.encode()) < 1_000
    assert record_limit == 10_000


@pytest.mark.asyncio
async def test_transport_rejects_catalog_over_new_body_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(sleeper_catalog(), separators=(",", ":")).encode()
    install_http_response(
        monkeypatch,
        FakeHttpResponse(body, content_length=_EXPECTED_BODY_LIMIT + 1),
    )
    provider = SleeperPlayerProvider(
        transport=AiohttpSleeperTransport(),
        cache_path=tmp_path / "provider-snapshots.sqlite3",
    )

    result = await provider.get_player_experience([])

    assert result["status"] == "unavailable"
    assert result["catalogPlayers"] == 0
    assert result["refreshFailed"] is True
    with sqlite3.connect(provider.cache_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)


@pytest.mark.asyncio
async def test_transport_rejects_stream_that_exceeds_reported_content_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_limit = 65_536
    install_http_response(
        monkeypatch,
        FakeHttpResponse(b"x" * (body_limit + 1), content_length=1),
    )
    provider = SleeperPlayerProvider(
        transport=AiohttpSleeperTransport(),
        cache_path=tmp_path / "provider-snapshots.sqlite3",
        max_body_bytes=body_limit,
    )

    result = await provider.get_player_experience([])

    assert result["status"] == "unavailable"
    assert result["catalogPlayers"] == 0
    assert result["refreshFailed"] is True
    with sqlite3.connect(provider.cache_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)


@pytest.mark.asyncio
async def test_source_catalog_over_normalized_limit_is_allowed_and_keeps_cache_key(
    tmp_path: Path,
) -> None:
    payload = {
        str(index): {
            "player_id": str(index),
            "full_name": f"Ignored Quarterback {index}",
            "position": "QB",
            "team": "SEA",
            "years_exp": 1,
        }
        for index in range(1, 10_001)
    }
    payload["10001"] = sleeper_catalog()["100"]
    cache_path = tmp_path / "provider-snapshots.sqlite3"
    provider = SleeperPlayerProvider(
        transport=FakeTransport(payload),
        cache_path=cache_path,
    )

    result = await provider.get_player_experience([])

    assert len(payload) == 10_001
    assert result["status"] == "success"
    assert result["catalogPlayers"] == 1
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute(
            "SELECT endpoint, variant, record_limit FROM snapshots"
        ).fetchall() == [("sleeper_players", "active", 10_000)]


@pytest.mark.asyncio
async def test_source_catalog_over_source_record_limit_fails_closed(tmp_path: Path) -> None:
    payload = {str(index): {} for index in range(1, 12_002)}
    payload["1"] = sleeper_catalog()["100"]
    provider = SleeperPlayerProvider(
        transport=FakeTransport(payload),
        cache_path=tmp_path / "provider-snapshots.sqlite3",
    )

    result = await provider.get_player_experience([])

    assert len(payload) == 12_001
    assert result["status"] == "unavailable"
    assert result["catalogPlayers"] == 0
    with sqlite3.connect(provider.cache_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)


@pytest.mark.asyncio
async def test_catalog_over_normalized_record_limit_fails_closed(tmp_path: Path) -> None:
    payload = {
        str(index): {
            "player_id": str(index),
            "full_name": f"Running Back {index}",
            "position": "RB",
            "team": "SF",
            "years_exp": 1,
        }
        for index in range(1, 10_002)
    }
    provider = SleeperPlayerProvider(
        transport=FakeTransport(payload),
        cache_path=tmp_path / "provider-snapshots.sqlite3",
    )

    result = await provider.get_player_experience([])

    assert len(payload) == 10_001
    assert result["status"] == "unavailable"
    assert result["catalogPlayers"] == 0
    with sqlite3.connect(provider.cache_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)


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
    assert transport.calls[0][1]["max_body_bytes"] == _EXPECTED_BODY_LIMIT
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
