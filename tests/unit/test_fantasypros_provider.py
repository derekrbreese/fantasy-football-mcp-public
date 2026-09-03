"""Tests for bounded, privacy-safe FantasyPros news and injury enrichment."""

from __future__ import annotations

import json
import sqlite3
import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.services import fantasypros_request_budget as request_budget_module
from src.services import fantasypros_snapshot_cache as snapshot_cache_module
from src.services.fantasypros_provider import FantasyProsProvider, FantasyProsProviderError
from src.services.fantasypros_request_budget import (
    FantasyProsDailyRequestBudget,
    FantasyProsRequestBudgetExhausted,
    FantasyProsRequestBudgetUnavailable,
)


@pytest.fixture(autouse=True)
def isolate_persistent_fantasypros_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        request_budget_module,
        "DEFAULT_REQUEST_BUDGET_PATH",
        tmp_path / "app-private" / "fantasypros-request-budget.json",
    )
    monkeypatch.setattr(
        snapshot_cache_module,
        "DEFAULT_SNAPSHOT_CACHE_PATH",
        tmp_path / "app-private" / "provider-snapshots.sqlite3",
    )


def _provider(**kwargs: Any) -> FantasyProsProvider:
    kwargs.setdefault("request_interval_seconds", 0.0)
    return FantasyProsProvider(**kwargs)


def test_budget_persists_only_minimal_private_metadata_across_restarts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app-private" / "fantasypros-request-budget.json"
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)

    budget = FantasyProsDailyRequestBudget(path=path, daily_limit=3)
    budget.reserve(now)
    budget.reserve(now + timedelta(minutes=1))
    restarted = FantasyProsDailyRequestBudget(path=path, daily_limit=3)
    restarted.reserve(now + timedelta(minutes=2))

    with pytest.raises(FantasyProsRequestBudgetExhausted) as raised:
        restarted.reserve(now + timedelta(minutes=3))

    assert raised.value.retry_at == datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "utcDate": "2026-09-01",
        "requestCount": 3,
    }
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_budget_resets_only_after_the_next_utc_day(tmp_path: Path) -> None:
    path = tmp_path / "app-private" / "fantasypros-request-budget.json"
    first_day = datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc)
    budget = FantasyProsDailyRequestBudget(path=path, daily_limit=1)
    budget.reserve(first_day)

    with pytest.raises(FantasyProsRequestBudgetExhausted):
        budget.reserve(first_day + timedelta(seconds=30))

    budget.reserve(first_day + timedelta(minutes=1))
    assert json.loads(path.read_text(encoding="utf-8"))["requestCount"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["utcDate"] == "2026-09-02"


@pytest.mark.parametrize(
    "stored",
    [
        {"schemaVersion": 1, "utcDate": "not-a-date", "requestCount": 1},
        {"schemaVersion": 1, "utcDate": "2026-09-01", "requestCount": -1},
        {
            "schemaVersion": 1,
            "utcDate": "2026-09-01",
            "requestCount": 1,
            "unexpected": "must-not-survive",
        },
    ],
)
def test_budget_fails_closed_for_malformed_state(
    tmp_path: Path,
    stored: dict[str, object],
) -> None:
    path = tmp_path / "app-private" / "fantasypros-request-budget.json"
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps(stored), encoding="utf-8")
    budget = FantasyProsDailyRequestBudget(path=path, daily_limit=3)

    with pytest.raises(FantasyProsRequestBudgetUnavailable):
        budget.reserve(datetime(2026, 9, 1, 16, tzinfo=timezone.utc))


def test_budget_fails_closed_if_the_clock_moves_behind_stored_date(tmp_path: Path) -> None:
    path = tmp_path / "app-private" / "fantasypros-request-budget.json"
    budget = FantasyProsDailyRequestBudget(path=path, daily_limit=3)
    budget.reserve(datetime(2026, 9, 2, 0, 1, tzinfo=timezone.utc))

    with pytest.raises(FantasyProsRequestBudgetUnavailable):
        budget.reserve(datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc))


def test_budget_fails_closed_when_atomic_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "app-private" / "fantasypros-request-budget.json"

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(request_budget_module.os, "replace", fail_replace)

    with pytest.raises(FantasyProsRequestBudgetUnavailable):
        FantasyProsDailyRequestBudget(path=path, daily_limit=3).reserve(
            datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
        )

    assert not path.exists()
    assert list(path.parent.glob(".fantasypros-budget-*.json")) == []


def test_explicit_budget_path_does_not_chmod_an_existing_shared_parent(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    path = parent / "fantasypros-request-budget.json"

    FantasyProsDailyRequestBudget(path=path, daily_limit=3).reserve(
        datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    )

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
        "consensus-rankings": _adp_payload(),
    }


def _projection_payload(*, scoring: str = "HALF") -> dict[str, Any]:
    return {
        "season": "2026",
        "week": "0",
        "count": "3",
        "positions": "RB,WR,TE",
        "scoring": scoring,
        "experts": [9, 22],
        "players": [
            {
                "fpid": "101",
                "name": "Jordan Alpha",
                "position_id": "RB",
                "team_id": "SF",
                "filename": "must-not-escape.php",
                "stats": {
                    "points": 201.25,
                    "points_half": 221.5,
                    "points_ppr": 241.75,
                    "rush_att": 181.25,
                    "rec_rec": 41.5,
                    "raw_note": "must-not-escape",
                },
            },
            {
                "fpid": "202",
                "name": "Case O'Neil",
                "position_id": "WR",
                "team_id": "NYJ",
                "filename": "must-not-escape.php",
                "stats": {
                    "points": 155.0,
                    "points_half": 190.25,
                    "points_ppr": 225.5,
                    "rush_att": 2.0,
                    "rec_rec": 70.5,
                },
            },
            {
                "fpid": "303",
                "name": "Invalid Tight End",
                "position_id": "TE",
                "team_id": "DAL",
                "stats": {"points": 99.0, "points_half": 110.0, "points_ppr": 121.0},
            },
        ],
    }


def _adp_payload(*, scoring: str = "HALF") -> dict[str, Any]:
    return {
        "sport": "NFL",
        "type": "ADP",
        "year": "2026",
        "week": "0",
        "position_id": "ALL",
        "scoring": scoring,
        "count": 2,
        "last_updated": "8/31",
        "players": [
            {
                "player_id": 101,
                "player_name": "Jordan Alpha",
                "player_position_id": "RB",
                "player_team_id": "SF",
                "player_yahoo_id": 501,
                "rank_ave": "18.5",
                "player_page_url": "must-not-escape",
            },
            {
                "player_id": 202,
                "player_name": "Case O'Neil",
                "player_position_id": "WR",
                "player_team_id": "NYJ",
                "player_yahoo_id": "502",
                "rank_ave": 73,
            },
        ],
    }


@pytest.mark.asyncio
async def test_half_ppr_adp_uses_scoring_specific_consensus_snapshot(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    source_payloads["projections"] = _projection_payload()
    source_payloads["consensus-rankings"] = _adp_payload()
    transport = FakeTransport(source_payloads)

    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
        projection_scoring="HALF",
    )

    adp_call = next(
        call for call in transport.calls if call["url"].endswith("/consensus-rankings")
    )
    assert adp_call["url"] == (
        "https://api.fantasypros.com/public/v2/json/nfl/2026/consensus-rankings"
    )
    assert adp_call["params"] == {
        "position": "ALL",
        "scoring": "HALF",
        "type": "ADP",
        "week": 0,
    }
    assert result["players"][0]["average_draft_position"] == 18.5
    assert result["players"][0]["adp_scoring"] == "HALF"
    assert result["players"][0]["yahoo_player_id"] == "501"
    assert result["adpEvidence"] == {
        "status": "available",
        "reason": None,
        "source": "FantasyPros",
        "season": 2026,
        "scoring": "HALF",
        "sourceAsOf": None,
        "fetchedAt": "2026-09-01T00:00:00Z",
        "stale": False,
        "refreshFailed": False,
        "availablePlayers": 2,
        "publicApiLimited": False,
    }
    assert "must-not-escape" not in repr(result)
    cache_path = snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute(
            "SELECT endpoint, variant, season FROM snapshots WHERE endpoint = 'adp'"
        ).fetchall() == [("adp", "preseason-half", 2026)]
        stored_adp = json.loads(
            connection.execute(
                "SELECT records_json FROM snapshots WHERE endpoint = 'adp'"
            ).fetchone()[0]
        )
    assert stored_adp[0] == {
        "id": 101,
        "name": "Jordan Alpha",
        "position": "RB",
        "team": "SF",
        "adp": 18.5,
        "yahooId": "501",
    }
    assert all(
        set(record) <= {"id", "name", "position", "team", "adp", "yahooId"}
        for record in stored_adp
    )

    restarted_transport = FakeTransport({})
    restarted = await _provider(
        api_key="different-secret",
        transport=restarted_transport,
        clock=lambda: datetime(2026, 9, 1, 0, 4, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
        projection_scoring="HALF",
    )

    assert restarted_transport.calls == []
    assert restarted["players"][0]["average_draft_position"] == 18.5
    assert restarted["players"][0]["yahoo_player_id"] == "501"


@pytest.mark.parametrize(
    ("raw_id", "expected"),
    [
        (501, "501"),
        ("502", "502"),
        (None, None),
        (True, None),
        (0, None),
        (-1, None),
        (1.5, None),
        ("0501", None),
        ("501.0", None),
        ("https://example.invalid/501?token=secret", None),
        (10_000_000_001, None),
    ],
)
def test_consensus_adp_yahoo_id_is_strictly_bounded(raw_id: object, expected: str | None) -> None:
    raw = _adp_payload()["players"][0]
    raw["player_yahoo_id"] = raw_id

    player = FantasyProsProvider._adp_player(raw)

    assert player is not None
    assert player.yahoo_id == expected


def test_legacy_adp_snapshot_without_yahoo_id_remains_compatible() -> None:
    records = FantasyProsProvider._records_from_snapshot(
        "adp",
        ({"id": 101, "name": "Jordan Alpha", "position": "RB", "team": "SF", "adp": 18.5},),
    )

    assert records[0].yahoo_id is None


@pytest.mark.parametrize("bad_id", [501, "0501", "501.0", "10000000001"])
def test_adp_snapshot_rejects_noncanonical_yahoo_id(bad_id: object) -> None:
    record = {
        "id": 101,
        "name": "Jordan Alpha",
        "position": "RB",
        "team": "SF",
        "adp": 18.5,
        "yahooId": bad_id,
    }

    with pytest.raises(snapshot_cache_module.FantasyProsSnapshotCacheUnavailable):
        snapshot_cache_module.FantasyProsSnapshotCache._validate_records("adp", (record,))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"year": "2025"},
        {"week": "1"},
        {"position_id": "RB"},
        {"scoring": "PPR"},
        {"type": "Preseason"},
    ],
)
async def test_half_ppr_adp_response_scope_mismatch_fails_closed(
    source_payloads: dict[str, dict[str, Any]],
    mutation: dict[str, Any],
) -> None:
    source_payloads["projections"] = _projection_payload()
    source_payloads["consensus-rankings"] = {**_adp_payload(), **mutation}

    result = await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
        projection_scoring="HALF",
    )

    assert "average_draft_position" not in result["players"][0]
    assert result["adpEvidence"]["status"] == "unavailable"
    assert result["adpEvidence"]["reason"] == "adp_unavailable"
    assert any("preseason ADP is temporarily unavailable" in item for item in result["warnings"])


@pytest.mark.asyncio
async def test_preseason_projections_use_explicit_contract_and_strict_allowlist(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    source_payloads["projections"] = _projection_payload()
    source_payloads["players"].update({"season": "2026", "week": "0"})
    transport = FakeTransport(source_payloads)
    provider = _provider(api_key="unit-test-secret", transport=transport, clock=lambda: now)

    result = await provider.get_player_updates(
        [
            {
                "name": "Jordan Alpha",
                "position": "RB",
                "team": "SF",
                "fantasypros_id": 101,
            },
            {
                "name": "Case O'Neil",
                "position": "WR",
                "team": "NYJ",
                "fantasypros_id": 202,
            },
            {"name": "A Quarterback", "position": "QB", "team": "BUF"},
        ],
        year=2026,
        week=0,
        projection_scoring="HALF",
    )

    projection_call = next(call for call in transport.calls if call["url"].endswith("/projections"))
    assert projection_call["url"] == (
        "https://api.fantasypros.com/public/v2/json/nfl/2026/projections"
    )
    assert projection_call["params"] == {
        "week": 0,
        "positions": "RB:WR:TE",
        "scoring": "HALF",
    }
    assert result["projectionEvidence"] == {
        "status": "available",
        "source": "FantasyPros",
        "season": 2026,
        "week": 0,
        "positions": ["RB", "WR", "TE"],
        "scoring": "HALF",
        "sourceAsOf": None,
        "fetchedAt": "2026-09-01T16:00:00Z",
        "stale": False,
        "refreshFailed": False,
        "availablePlayers": 2,
        "experienceYearsAvailable": False,
        "returnedCount": 3,
        "reportedCount": 3,
        "reportedLimit": None,
        "publicApiLimited": False,
    }
    assert result["adpEvidence"]["status"] == "available"
    assert result["adpEvidence"]["reason"] is None
    assert result["players"][0]["average_draft_position"] == 18.5
    expected_projection = {
        "projected_points": 221.5,
        "projected_opportunities": 222.75,
        "projection_opportunity_kind": "touches",
        "projection_source": "FantasyPros",
        "projection_season": 2026,
        "projection_scoring": "HALF",
        "projection_source_as_of": None,
        "projection_fetched_at": "2026-09-01T16:00:00Z",
        "projection_stale": False,
    }
    assert {
        key: result["players"][0][key] for key in expected_projection
    } == expected_projection
    assert result["players"][1]["projected_opportunities"] == 70.5
    assert result["players"][1]["projection_opportunity_kind"] == "receptions"
    assert "projected_points" not in result["players"][2]
    serialized = repr(result)
    for forbidden in (
        "must-not-escape",
        "filename",
        "raw_note",
        "experience_years",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scoring", "expected_adp", "expected_status", "expected_reason"),
    [
        ("STD", 18.0, "available", None),
        ("PPR", 23.0, "available", None),
        ("HALF", 18.5, "available", None),
    ],
)
async def test_adp_uses_only_explicit_scoring_field_and_same_season(
    source_payloads: dict[str, dict[str, Any]],
    scoring: str,
    expected_adp: float | None,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    source_payloads["players"].update(
        {
            "season": "2026",
            "week": "0",
            "public_api_limited": True,
        }
    )
    source_payloads["players"]["players"][0].update(
        {"rank_adp": 18, "rank_adp_ppr": 23, "rank_ecr": 1}
    )
    source_payloads["projections"] = _projection_payload(scoring=scoring)
    result = await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).get_player_updates(
        [
            {
                "name": "Jordan Alpha",
                "position": "RB",
                "team": "SF",
                "fantasypros_id": 101,
            }
        ],
        year=2026,
        projection_scoring=scoring,
    )

    player = result["players"][0]
    if expected_adp is None:
        assert "average_draft_position" not in player
    else:
        assert player["average_draft_position"] == expected_adp
        assert player["adp_source"] == "FantasyPros"
        assert player["adp_season"] == 2026
        assert player["adp_scoring"] == scoring
        assert player["adp_source_as_of"] is None
        assert player["adp_fetched_at"] == "2026-09-01T00:00:00Z"
        assert player["adp_stale"] is False
    assert result["adpEvidence"]["status"] == expected_status
    assert result["adpEvidence"]["reason"] == expected_reason
    assert result["adpEvidence"]["publicApiLimited"] is (scoring != "HALF")
    assert "rank_ecr" not in repr(result)


@pytest.mark.asyncio
async def test_catalog_adp_rejects_unverified_season_without_losing_projections(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    source_payloads["players"].update({"season": "2025", "week": "0"})
    source_payloads["players"]["players"][0].update({"rank_adp": 18, "rank_adp_ppr": 23})
    source_payloads["projections"] = _projection_payload(scoring="PPR")
    result = await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).get_player_updates(
        [
            {
                "name": "Jordan Alpha",
                "position": "RB",
                "team": "SF",
                "fantasypros_id": 101,
            }
        ],
        year=2026,
        projection_scoring="PPR",
    )

    assert result["players"][0]["projected_points"] == 241.75
    assert "average_draft_position" not in result["players"][0]
    assert result["adpEvidence"]["status"] == "unavailable"
    assert result["adpEvidence"]["reason"] == "catalog_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "warning_fragment"),
    [
        ({"season": "2025"}, "preseason projections is temporarily unavailable"),
        ({"week": "1"}, "preseason projections is temporarily unavailable"),
        ({"scoring": "PPR"}, "preseason projections is temporarily unavailable"),
        ({"positions": "RB:WR"}, "preseason projections is temporarily unavailable"),
        ({"positions": "TE,RB,WR"}, "preseason projections is temporarily unavailable"),
    ],
)
async def test_projection_response_scope_mismatch_fails_closed_without_zero_evidence(
    source_payloads: dict[str, dict[str, Any]],
    mutation: dict[str, Any],
    warning_fragment: str,
) -> None:
    payload = _projection_payload()
    payload.update(mutation)
    source_payloads["projections"] = payload
    result = await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
        projection_scoring="HALF",
    )

    assert any(warning_fragment in warning for warning in result["warnings"])
    assert result["projectionEvidence"]["status"] == "unavailable"
    assert "projected_points" not in result["players"][0]


@pytest.mark.asyncio
async def test_projection_snapshot_schema_migration_preserves_existing_snapshots(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    cache_path = tmp_path / "private" / "fantasypros-snapshots.sqlite3"
    cache_path.parent.mkdir(mode=0o700)
    with sqlite3.connect(cache_path) as connection:
        connection.executescript(
            """
            PRAGMA user_version = 3;
            CREATE TABLE snapshots (
                endpoint TEXT NOT NULL,
                variant TEXT NOT NULL,
                season INTEGER NOT NULL,
                week INTEGER NOT NULL,
                request_limit INTEGER NOT NULL,
                record_limit INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL,
                records_json TEXT NOT NULL CHECK(length(records_json) <= 2000000),
                truncated INTEGER NOT NULL CHECK(truncated IN (0, 1)),
                returned_count INTEGER NOT NULL CHECK(returned_count BETWEEN 0 AND 100000),
                reported_count INTEGER CHECK(reported_count BETWEEN 0 AND 10000000),
                reported_limit INTEGER CHECK(reported_limit BETWEEN 1 AND 100000),
                public_api_limited INTEGER NOT NULL CHECK(public_api_limited IN (0, 1)),
                PRIMARY KEY (endpoint, variant, season, week, request_limit, record_limit),
                CHECK(endpoint IN (
                    'players', 'injuries', 'news', 'projections', 'sleeper_players'
                )),
                CHECK(variant IN (
                    'catalog', 'catalog-season', 'weekly', 'recent',
                    'preseason-std', 'preseason-half', 'preseason-ppr', 'active'
                ))
            ) WITHOUT ROWID;
            """
        )
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "players",
                "catalog",
                0,
                0,
                0,
                2500,
                1788288000,
                '[{"id":101,"name":"Jordan Alpha","position":"RB","team":"SF"}]',
                0,
                1,
                1,
                None,
                0,
            ),
        )

    cache = snapshot_cache_module.FantasyProsSnapshotCache(path=cache_path)
    old_key = snapshot_cache_module.FantasyProsSnapshotKey(
        "players", "catalog", record_limit=2500
    )
    assert cache.load(old_key) is not None
    projection_key = snapshot_cache_module.FantasyProsSnapshotKey(
        "projections", "preseason-half", season=2026, week=0, record_limit=2500
    )
    cache.save(
        projection_key,
        snapshot_cache_module.FantasyProsSnapshot(
            records=(
                {
                    "id": 101,
                    "name": "Jordan Alpha",
                    "position": "RB",
                    "team": "SF",
                    "points": 221.5,
                    "opportunities": 222.75,
                    "opportunityKind": "touches",
                },
            ),
            fetched_at=datetime(2026, 9, 1, 18, 40, tzinfo=timezone.utc),
            truncated=False,
            returned_count=1,
            reported_count=1,
            reported_limit=None,
            public_api_limited=False,
        ),
    )

    migrated = cache.load(old_key)
    assert migrated is not None
    assert migrated.records == (
        {"id": 101, "name": "Jordan Alpha", "position": "RB", "team": "SF"},
    )
    assert cache.load(projection_key) is not None
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2

    source_payloads["projections"] = _projection_payload()
    transport = FakeTransport(source_payloads)
    result = await _provider(
        api_key="secret",
        transport=transport,
        snapshot_cache_path=cache_path,
        clock=lambda: datetime(2026, 9, 1, 18, 41, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
        projection_scoring="HALF",
    )

    assert {call["url"].rsplit("/", 1)[-1] for call in transport.calls} == {
        "injuries",
        "news",
        "consensus-rankings",
    }
    assert result["players"][0]["identityResolved"] is True
    assert result["players"][0]["projected_points"] == 221.5
    assert result["players"][0]["average_draft_position"] == 18.5
    assert result["adpEvidence"]["reason"] is None


def test_default_cache_copies_legacy_fantasypros_database_to_provider_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_path = tmp_path / "fantasypros-snapshots.sqlite3"
    destination = tmp_path / "provider-snapshots.sqlite3"
    key = snapshot_cache_module.FantasyProsSnapshotKey(
        "players", "catalog", record_limit=2500
    )
    snapshot = snapshot_cache_module.FantasyProsSnapshot(
        records=(
            {"id": 101, "name": "Jordan Alpha", "position": "RB", "team": "SF"},
        ),
        fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        truncated=False,
        returned_count=1,
        reported_count=1,
        reported_limit=None,
        public_api_limited=False,
    )
    snapshot_cache_module.FantasyProsSnapshotCache(path=legacy_path).save(key, snapshot)
    monkeypatch.setattr(
        snapshot_cache_module, "LEGACY_FANTASYPROS_SNAPSHOT_CACHE_PATH", legacy_path
    )
    monkeypatch.setattr(
        snapshot_cache_module, "DEFAULT_PROVIDER_SNAPSHOT_CACHE_PATH", destination
    )
    monkeypatch.setattr(snapshot_cache_module, "DEFAULT_SNAPSHOT_CACHE_PATH", destination)

    migrated = snapshot_cache_module.FantasyProsSnapshotCache().load(key)

    assert migrated == snapshot
    assert destination.is_file()
    assert legacy_path.is_file()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_projection_and_adp_snapshots_are_private_and_reused_after_restart(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    source_payloads["players"].update(
        {"season": "2026", "week": "0", "public_api_limited": True}
    )
    source_payloads["players"]["players"][0].update(
        {"rank_adp": 18, "rank_adp_ppr": 23, "rank_ecr": 1}
    )
    source_payloads["projections"] = _projection_payload(scoring="PPR")
    identity = [
        {
            "name": "Jordan Alpha",
            "position": "RB",
            "team": "SF",
            "fantasypros_id": 101,
        }
    ]
    first_transport = FakeTransport(source_payloads)
    first = await _provider(
        api_key="first-secret",
        transport=first_transport,
        clock=lambda: now,
    ).get_player_updates(identity, year=2026, projection_scoring="PPR")

    restarted_transport = FakeTransport(source_payloads)
    restarted = await _provider(
        api_key="second-secret",
        transport=restarted_transport,
        clock=lambda: now,
    ).get_player_updates(identity, year=2026, projection_scoring="PPR")

    assert len(first_transport.calls) == 4
    assert restarted_transport.calls == []
    assert restarted["players"][0]["projected_points"] == 241.75
    assert restarted["players"][0]["average_draft_position"] == 23.0
    assert restarted["projectionEvidence"]["status"] == "available"
    assert restarted["adpEvidence"]["status"] == "available"
    cache_path = snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH
    with sqlite3.connect(cache_path) as connection:
        rows = connection.execute(
            "SELECT endpoint, variant, season, records_json FROM snapshots "
            "WHERE endpoint IN ('players', 'projections') ORDER BY endpoint"
        ).fetchall()
    assert {(endpoint, variant, season) for endpoint, variant, season, _ in rows} == {
        ("players", "catalog-season", 2026),
        ("projections", "preseason-ppr", 2026),
    }
    serialized_rows = repr(rows)
    assert "rank_ecr" not in serialized_rows
    assert "filename" not in serialized_rows
    stored_bytes = cache_path.read_bytes()
    for forbidden in (b"first-secret", b"second-secret", b"https://", b"x-api-key"):
        assert forbidden not in stored_bytes
    assert first["coverage"]["playerCatalog"]["publicApiLimited"] is True


@pytest.mark.asyncio
async def test_projection_refresh_failure_uses_bounded_stale_snapshot_with_provenance(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    source_payloads["players"].update({"season": "2026", "week": "0"})
    source_payloads["projections"] = _projection_payload()
    identity = [
        {
            "name": "Jordan Alpha",
            "position": "RB",
            "team": "SF",
            "fantasypros_id": 101,
        }
    ]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026, projection_scoring="HALF")

    current[0] += timedelta(days=2)
    refreshed = deepcopy(source_payloads)
    refreshed["projections"] = RuntimeError("raw provider failure must not escape")
    result = await _provider(
        api_key="secret",
        transport=FakeTransport(refreshed),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026, projection_scoring="HALF")

    assert result["projectionEvidence"]["status"] == "stale"
    assert result["projectionEvidence"]["refreshFailed"] is True
    assert result["players"][0]["projected_points"] == 221.5
    assert result["players"][0]["projection_stale"] is True
    assert any("preseason projections refresh failed" in item for item in result["warnings"])
    assert "raw provider failure" not in repr(result)


@pytest.mark.asyncio
async def test_provider_uses_official_contract_and_returns_only_allowlisted_fields(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    transport = FakeTransport(source_payloads)
    provider = _provider(
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
    provider = _provider(
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
    provider = _provider(
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
    provider = _provider(
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
    provider = _provider(
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
async def test_provider_spaces_external_requests_to_the_public_api_limit(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    monotonic = [0.0]
    starts: list[float] = []
    sleeps: list[float] = []

    class TimedTransport(FakeTransport):
        async def get_json(self, *args, **kwargs):
            starts.append(monotonic[0])
            return await super().get_json(*args, **kwargs)

    async def advance(delay: float) -> None:
        sleeps.append(delay)
        monotonic[0] += delay

    provider = _provider(
        api_key="secret",
        transport=TimedTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        request_interval_seconds=1.0,
        monotonic=lambda: monotonic[0],
        sleep=advance,
    )

    result = await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
    )

    assert result["status"] == "success"
    assert starts == [0.0, 1.0, 2.0]
    assert sleeps == [1.0, 1.0]


@pytest.mark.asyncio
async def test_provider_enforces_persistent_daily_budget_before_network_and_returns_unknown(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    budget_path = tmp_path / "app-private" / "fantasypros-request-budget.json"
    first_transport = FakeTransport(source_payloads)
    first = _provider(
        api_key="secret",
        transport=first_transport,
        clock=lambda: now,
        daily_request_limit=3,
        daily_budget_path=budget_path,
        snapshot_cache_path=tmp_path / "first-snapshots.sqlite3",
    )
    await first.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
    )
    assert len(first_transport.calls) == 3

    restarted_transport = FakeTransport(source_payloads)
    restarted = _provider(
        api_key="secret",
        transport=restarted_transport,
        clock=lambda: now,
        daily_request_limit=3,
        daily_budget_path=budget_path,
        snapshot_cache_path=tmp_path / "restarted-snapshots.sqlite3",
    )
    result = await restarted.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}],
        year=2026,
    )

    assert restarted_transport.calls == []
    assert result["status"] == "degraded"
    assert result["warnings"] == [
        "FantasyPros daily request budget is exhausted; missing data remains unknown until the next UTC day"
    ]
    assert result["players"][0]["identityResolved"] is False
    assert result["players"][0]["injury_status"] == "unknown"
    assert result["players"][0]["recentNews"] == []


@pytest.mark.asyncio
async def test_provider_counts_targeted_lookups_in_the_same_daily_budget(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    transport = FakeTransport(
        {
            "players": {"sport": "NFL", "players": []},
            "injuries": {"sport": "NFL", "injuries": []},
            "news": {
                "sport": "NFL",
                "items": [
                    {
                        "player_id": player_id,
                        "title": f"Player {player_id} update",
                        "created": "2026-09-01 15:00:00",
                        "categories": ["News"],
                    }
                    for player_id in (901, 902)
                ],
            },
            "players:901": {
                "sport": "NFL",
                "players": [
                    {
                        "player_id": 901,
                        "player_name": "First Target",
                        "position_id": "WR",
                        "team_id": "SF",
                    }
                ],
            },
            "players:902": {
                "sport": "NFL",
                "players": [
                    {
                        "player_id": 902,
                        "player_name": "Second Target",
                        "position_id": "WR",
                        "team_id": "SEA",
                    }
                ],
            },
        }
    )
    provider = _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: now,
        daily_request_limit=4,
        daily_budget_path=tmp_path / "app-private" / "fantasypros-request-budget.json",
    )

    result = await provider.get_player_updates(
        [
            {"name": "First Target", "position": "WR", "team": "SF"},
            {"name": "Second Target", "position": "WR", "team": "SEA"},
        ],
        year=2026,
    )

    assert len(transport.calls) == 4
    assert [call["params"].get("player") for call in transport.calls] == [None, None, None, 901]
    assert result["status"] == "degraded"
    assert result["warnings"] == [
        "FantasyPros daily request budget is exhausted; missing data remains unknown until the next UTC day"
    ]
    assert result["players"][0]["identityResolved"] is True
    assert result["players"][1]["identityResolved"] is False


@pytest.mark.asyncio
async def test_provider_budget_failure_deadline_is_not_extended_by_repeated_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    monotonic = [0.0]
    transport = FakeTransport(source_payloads)
    provider = _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
        monotonic=lambda: monotonic[0],
        failure_backoff_seconds=60.0,
        daily_budget_path=tmp_path / "app-private" / "fantasypros-request-budget.json",
    )
    reserve_calls = 0

    def unavailable(_now: datetime) -> None:
        nonlocal reserve_calls
        reserve_calls += 1
        raise FantasyProsRequestBudgetUnavailable

    monkeypatch.setattr(provider._daily_request_budget, "reserve", unavailable)
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]

    first = await provider.get_player_updates(identity, year=2026)
    monotonic[0] = 30.0
    second = await provider.get_player_updates(identity, year=2026)
    monotonic[0] = 59.0
    third = await provider.get_player_updates(identity, year=2026)

    assert first["warnings"] == second["warnings"] == third["warnings"] == [
        "FantasyPros daily request budget is unavailable; missing data remains unknown"
    ]
    assert reserve_calls == 1
    assert transport.calls == []

    monotonic[0] = 61.0
    await provider.get_player_updates(identity, year=2026)
    assert reserve_calls == 2


@pytest.mark.asyncio
async def test_provider_backs_off_failed_endpoints_without_retry_storms() -> None:
    monotonic = [0.0]
    transport = FakeTransport(
        {
            "players": RuntimeError("rate limited with secret details"),
            "injuries": RuntimeError("rate limited with secret details"),
            "news": RuntimeError("rate limited with secret details"),
        }
    )
    provider = _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        request_interval_seconds=0.0,
        failure_backoff_seconds=60.0,
        monotonic=lambda: monotonic[0],
    )
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]

    first = await provider.get_player_updates(identity, year=2026)
    second = await provider.get_player_updates(identity, year=2026)

    assert first["status"] == second["status"] == "degraded"
    assert len(transport.calls) == 3
    assert "secret details" not in repr(second)

    monotonic[0] = 30.0
    await provider.get_player_updates(identity, year=2026)
    assert len(transport.calls) == 3

    monotonic[0] = 61.0
    await provider.get_player_updates(identity, year=2026)
    assert len(transport.calls) == 6


@pytest.mark.asyncio
async def test_provider_uses_longer_backoff_for_public_api_rate_limits() -> None:
    monotonic = [0.0]
    rate_limit = FantasyProsProviderError(
        "FantasyPros returned HTTP status 429",
        status_code=429,
    )
    transport = FakeTransport(
        {"players": rate_limit, "injuries": rate_limit, "news": rate_limit}
    )
    provider = _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        failure_backoff_seconds=60.0,
        rate_limit_backoff_seconds=900.0,
        monotonic=lambda: monotonic[0],
    )
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]

    first = await provider.get_player_updates(identity, year=2026)
    assert first["warnings"] == [
        "FantasyPros player catalog is rate-limited; missing data remains unknown",
        "FantasyPros injuries is rate-limited; missing data remains unknown",
        "FantasyPros news is rate-limited; missing data remains unknown",
    ]
    assert len(transport.calls) == 1
    budget_state = json.loads(provider._daily_request_budget.path.read_text(encoding="utf-8"))
    assert budget_state["requestCount"] == 1
    monotonic[0] = 120.0
    backed_off = await provider.get_player_updates(identity, year=2026)
    assert backed_off["warnings"] == first["warnings"]
    assert len(transport.calls) == 1
    assert json.loads(
        provider._daily_request_budget.path.read_text(encoding="utf-8")
    )["requestCount"] == 1

    monotonic[0] = 901.0
    await provider.get_player_updates(identity, year=2026)
    assert len(transport.calls) == 2
    assert json.loads(
        provider._daily_request_budget.path.read_text(encoding="utf-8")
    )["requestCount"] == 2


@pytest.mark.asyncio
async def test_provider_caches_fast_data_and_player_directory_on_separate_ttls(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, tzinfo=timezone.utc)]
    transport = FakeTransport(source_payloads)
    provider = _provider(
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
    provider = _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
        cache_ttl_seconds=1,
        player_cache_ttl_seconds=86_400,
        max_cache_entries=16,
    )

    for batch in range(10):
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
    provider = _provider(
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
    provider = _provider(api_key="secret", transport=transport, clock=lambda: now)

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
        "stale": False,
        "refreshFailed": False,
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
async def test_limited_catalog_bounds_and_caches_targeted_news_identity_lookups() -> None:
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
    for player_id in range(100, 102):
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
    provider = _provider(
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
    assert [call["params"]["player"] for call in targeted_calls] == [100, 101]
    assert first["status"] == "degraded"
    assert first["coverage"]["targetedPlayerLookups"] == {
        "attempted": 2,
        "resolved": 2,
        "capped": True,
    }
    assert any("player catalog coverage" in warning for warning in first["warnings"])
    assert any("news coverage" in warning for warning in first["warnings"])
    assert any("news identity coverage" in warning for warning in first["warnings"])
    assert all(player["identityResolved"] is True for player in first["players"][:2])
    assert all(player["news_fresh"] is True for player in first["players"][:2])
    assert all(player["identityResolved"] is False for player in first["players"][2:])
    assert all(player["news_fresh"] is False for player in first["players"][2:])
    assert "example.invalid" not in repr(first)
    assert "must-not-escape" not in repr(first)

    call_count = len(transport.calls)
    await provider.get_player_updates(candidates, year=2026)
    assert len(transport.calls) == call_count

    current[0] += timedelta(hours=6)
    await provider.get_player_updates(candidates, year=2026)
    new_calls = transport.calls[call_count:]
    assert {call["url"].rsplit("/", 1)[-1] for call in new_calls} == {
        "players",
        "injuries",
        "news",
    }


@pytest.mark.asyncio
async def test_resolved_candidates_skip_irrelevant_news_identity_lookups() -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    unrelated_news = [
        {
            "player_id": player_id,
            "title": f"Unrelated player {player_id} update",
            "created": "2026-09-01 12:00:00",
            "categories": ["News"],
        }
        for player_id in range(200, 210)
    ]
    transport = FakeTransport(
        {
            "players": {
                "sport": "NFL",
                "count": 500,
                "public_api_limited": True,
                "players": [
                    {
                        "player_id": 101,
                        "player_name": "Resolved Candidate",
                        "position_id": "WR",
                        "team_id": "SF",
                    }
                ],
            },
            "injuries": {"sport": "NFL", "count": 0, "injuries": []},
            "news": {
                "sport": "NFL",
                "count": len(unrelated_news),
                "public_api_limited": True,
                "items": unrelated_news,
            },
        }
    )
    provider = _provider(api_key="secret", transport=transport, clock=lambda: now)

    result = await provider.get_player_updates(
        [{"name": "Resolved Candidate", "position": "WR", "team": "SF"}],
        year=2026,
    )

    assert len(transport.calls) == 3
    assert result["players"][0]["identityResolved"] is True
    assert result["coverage"]["targetedPlayerLookups"] == {
        "attempted": 0,
        "resolved": 0,
        "capped": False,
    }
    assert result["warnings"] == [
        "FantasyPros player catalog coverage is limited by the public API",
        "FantasyPros news coverage is limited by the public API",
    ]


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
    provider = _provider(
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


@pytest.mark.asyncio
async def test_normalized_snapshots_are_private_and_reused_across_restarts(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    first_transport = FakeTransport(source_payloads)
    first = _provider(
        api_key="unit-test-secret",
        transport=first_transport,
        clock=lambda: now,
    )

    first_result = await first.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    cache_path = snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH
    budget_count = json.loads(
        first._daily_request_budget.path.read_text(encoding="utf-8")
    )["requestCount"]
    restarted_transport = FakeTransport(source_payloads)
    restarted = _provider(
        api_key="different-test-secret",
        transport=restarted_transport,
        clock=lambda: now,
    )
    restarted_result = await restarted.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert first_result["players"] == restarted_result["players"]
    assert len(first_transport.calls) == 3
    assert restarted_transport.calls == []
    assert json.loads(
        restarted._daily_request_budget.path.read_text(encoding="utf-8")
    )["requestCount"] == budget_count
    assert stat.S_IMODE(cache_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
    assert not cache_path.with_name(f"{cache_path.name}-wal").exists()
    assert not cache_path.with_name(f"{cache_path.name}-journal").exists()

    with sqlite3.connect(cache_path) as connection:
        rows = connection.execute(
            "SELECT endpoint, variant, records_json FROM snapshots ORDER BY endpoint"
        ).fetchall()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "delete"
    assert {(endpoint, variant) for endpoint, variant, _records in rows} == {
        ("injuries", "weekly"),
        ("news", "recent"),
        ("players", "catalog"),
    }
    expected_fields = {
        "players": {"id", "name", "position", "team"},
        "injuries": {"id", "name", "position", "team", "status", "updatedAt"},
        "news": {"id", "headline", "category", "publishedAt"},
    }
    for endpoint, _variant, stored_json in rows:
        records = json.loads(stored_json)
        assert all(set(record) == expected_fields[endpoint] for record in records)

    stored_bytes = cache_path.read_bytes()
    for forbidden in (
        b"unit-test-secret",
        b"different-test-secret",
        b"must-not-escape",
        b"raw medical commentary",
        b"example.invalid",
        b"include_probabilities",
        b"order_by",
        b"x-api-key",
        b"https://",
    ):
        assert forbidden not in stored_bytes


@pytest.mark.asyncio
async def test_explicit_snapshot_path_does_not_chmod_existing_shared_parent(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    cache_path = parent / "fantasypros-snapshots.sqlite3"
    provider = _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
        snapshot_cache_path=cache_path,
    )

    await provider.get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_default_snapshot_cache_tightens_existing_app_directory(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    parent = snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH.parent
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)

    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_snapshot_symlink_is_not_followed_and_live_result_survives(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    target = parent / "unrelated.txt"
    sentinel = b"unrelated data must remain unchanged"
    target.write_bytes(sentinel)
    cache_path = parent / "snapshots.sqlite3"
    cache_path.symlink_to(target)
    transport = FakeTransport(source_payloads)

    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
        snapshot_cache_path=cache_path,
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert result["status"] == "success"
    assert len(transport.calls) == 3
    assert cache_path.is_symlink()
    assert target.read_bytes() == sentinel


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["corrupt", "wrong-schema"])
async def test_unusable_snapshot_database_is_a_miss_without_losing_live_data(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
    cache_state: str,
) -> None:
    cache_path = tmp_path / cache_state / "snapshots.sqlite3"
    cache_path.parent.mkdir()
    if cache_state == "corrupt":
        cache_path.write_bytes(b"not a sqlite database")
    else:
        with sqlite3.connect(cache_path) as connection:
            connection.execute("PRAGMA user_version = 99")
    transport = FakeTransport(source_payloads)

    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
        snapshot_cache_path=cache_path,
    ).get_player_updates(
        [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}], year=2026
    )

    assert result["status"] == "success"
    assert result["players"][0]["injury_status"] == "questionable"
    assert len(result["players"][0]["recentNews"]) == 1
    assert len(transport.calls) == 3


@pytest.mark.asyncio
async def test_persistent_snapshots_preserve_separate_resource_ttls(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    first = _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    )
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await first.get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    restarted_transport = FakeTransport(source_payloads)
    restarted = _provider(
        api_key="secret",
        transport=restarted_transport,
        clock=lambda: current[0],
    )
    result = await restarted.get_player_updates(identity, year=2026)

    assert result["status"] == "success"
    assert [call["url"].rsplit("/", 1)[-1] for call in restarted_transport.calls] == [
        "injuries",
        "news",
    ]
    assert result["coverage"]["playerCatalog"]["fetchedAt"] == (
        "2026-09-01T16:00:00Z"
    )
    assert result["coverage"]["injuries"]["fetchedAt"] == "2026-09-01T16:06:00Z"
    assert result["coverage"]["news"]["fetchedAt"] == "2026-09-01T16:06:00Z"


@pytest.mark.asyncio
async def test_stale_last_known_good_snapshots_survive_refresh_failures(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    failure = RuntimeError("response details must not escape")
    transport = FakeTransport(
        {
            "players": source_payloads["players"],
            "injuries": failure,
            "news": failure,
        }
    )
    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert result["status"] == "degraded"
    assert [call["url"].rsplit("/", 1)[-1] for call in transport.calls] == [
        "injuries",
        "news",
    ]
    assert result["players"][0]["identityResolved"] is True
    assert result["players"][0]["injury_status"] == "unknown"
    assert result["players"][0]["injury_source"] is None
    assert result["players"][0]["injury_fresh"] is False
    assert result["players"][0]["news_source"] is None
    assert result["players"][0]["news_fresh"] is False
    assert result["players"][0]["recentNews"] == []
    assert result["coverage"]["playerCatalog"]["stale"] is False
    for resource in ("injuries", "news"):
        assert result["coverage"][resource]["stale"] is True
        assert result["coverage"][resource]["refreshFailed"] is True
    assert any("injuries refresh failed" in warning for warning in result["warnings"])
    assert any("news refresh failed" in warning for warning in result["warnings"])
    assert all("stale snapshot" in warning for warning in result["warnings"])
    assert "response details" not in repr(result)


@pytest.mark.asyncio
async def test_snapshots_older_than_retention_ceiling_are_not_used_as_evidence(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(days=8)
    failure = RuntimeError("provider unavailable")
    transport = FakeTransport(
        {"players": failure, "injuries": failure, "news": failure}
    )
    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert len(transport.calls) == 3
    assert result["status"] == "degraded"
    assert result["players"][0]["identityResolved"] is False
    assert result["players"][0]["injury_status"] == "unknown"
    assert result["players"][0]["recentNews"] == []
    for resource in ("playerCatalog", "injuries", "news"):
        assert result["coverage"][resource]["stale"] is False
        assert result["coverage"][resource]["refreshFailed"] is True
        assert result["coverage"][resource]["fetchedAt"] is None


@pytest.mark.asyncio
async def test_rate_limit_uses_stale_snapshots_and_stops_queued_refreshes(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    rate_limit = FantasyProsProviderError("HTTP 429", status_code=429)
    transport = FakeTransport(
        {
            "players": source_payloads["players"],
            "injuries": rate_limit,
            "news": source_payloads["news"],
        }
    )
    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert len(transport.calls) == 1
    assert transport.calls[0]["url"].endswith("/injuries")
    for resource in ("injuries", "news"):
        assert result["coverage"][resource]["stale"] is True
        assert result["coverage"][resource]["refreshFailed"] is True
    assert sum("rate-limited" in warning for warning in result["warnings"]) == 2
    assert all("stale snapshot" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_partial_player_catalog_uses_short_refresh_ttl(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    source_payloads["players"].update(
        {"count": 502, "limit": 10, "public_api_limited": True}
    )
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    transport = FakeTransport(source_payloads)
    await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert {call["url"].rsplit("/", 1)[-1] for call in transport.calls} == {
        "players",
        "injuries",
        "news",
    }


@pytest.mark.asyncio
async def test_complete_public_limited_catalog_uses_long_refresh_ttl(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    source_payloads["players"].update(
        {
            "count": 500,
            "public_api_limited": True,
            "players": [
                {
                    "player_id": player_id,
                    "player_name": f"Player {player_id}",
                    "position_id": "RB",
                    "team_id": "SF",
                }
                for player_id in range(1, 501)
            ],
        }
    )
    identity = [{"name": "Player 1", "position": "RB", "team": "SF"}]
    first_result = await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    restarted_transport = FakeTransport(source_payloads)
    restarted_result = await _provider(
        api_key="secret",
        transport=restarted_transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert [call["url"].rsplit("/", 1)[-1] for call in restarted_transport.calls] == [
        "injuries",
        "news",
    ]
    assert first_result["coverage"]["playerCatalog"] == {
        "fetchedAt": "2026-09-01T16:00:00Z",
        "returned": 500,
        "reportedCount": 500,
        "reportedLimit": None,
        "publicApiLimited": True,
        "stale": False,
        "refreshFailed": False,
    }
    assert restarted_result["coverage"]["playerCatalog"] == (
        first_result["coverage"]["playerCatalog"]
    )


@pytest.mark.asyncio
async def test_daily_budget_exhaustion_uses_stale_snapshots_without_network(
    tmp_path: Path,
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    budget_path = tmp_path / "app-private" / "budget.json"
    cache_path = tmp_path / "app-private" / "snapshots.sqlite3"
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: current[0],
        daily_request_limit=3,
        daily_budget_path=budget_path,
        snapshot_cache_path=cache_path,
    ).get_player_updates(identity, year=2026)

    current[0] += timedelta(minutes=6)
    transport = FakeTransport(source_payloads)
    result = await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: current[0],
        daily_request_limit=3,
        daily_budget_path=budget_path,
        snapshot_cache_path=cache_path,
    ).get_player_updates(identity, year=2026)

    assert transport.calls == []
    assert result["status"] == "degraded"
    assert result["warnings"] == [
        "FantasyPros daily request budget is exhausted; using stale snapshots where "
        "available and missing data remains unknown until the next UTC day"
    ]
    assert result["players"][0]["identityResolved"] is True
    assert result["players"][0]["injury_status"] == "unknown"
    assert result["players"][0]["news_fresh"] is False
    assert result["players"][0]["recentNews"] == []
    assert result["coverage"]["playerCatalog"]["stale"] is False
    for resource in ("injuries", "news"):
        assert result["coverage"][resource]["stale"] is True
        assert result["coverage"][resource]["refreshFailed"] is True
    assert json.loads(budget_path.read_text(encoding="utf-8"))["requestCount"] == 3


@pytest.mark.asyncio
async def test_snapshot_keys_isolate_injury_year_and_week(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    now = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    await _provider(
        api_key="secret",
        transport=FakeTransport(source_payloads),
        clock=lambda: now,
    ).get_player_updates(identity, year=2025, week=17)

    transport = FakeTransport(source_payloads)
    await _provider(
        api_key="secret",
        transport=transport,
        clock=lambda: now,
    ).get_player_updates(identity, year=2026, week=1)

    assert len(transport.calls) == 1
    assert transport.calls[0]["url"].endswith("/injuries")
    assert transport.calls[0]["params"]["year"] == 2026
    assert transport.calls[0]["params"]["week"] == 1
    with sqlite3.connect(snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH) as connection:
        injury_scopes = connection.execute(
            "SELECT season, week FROM snapshots WHERE endpoint = 'injuries' ORDER BY season, week"
        ).fetchall()
    assert injury_scopes == [(2025, 17), (2026, 1)]


@pytest.mark.asyncio
async def test_targeted_player_lookups_remain_memory_only_and_stale_news_cannot_trigger_them(
    source_payloads: dict[str, dict[str, Any]],
) -> None:
    current = [datetime(2026, 9, 1, 16, tzinfo=timezone.utc)]
    payloads: dict[str, dict[str, Any] | Exception] = {
        "players": {"sport": "NFL", "count": 0, "players": []},
        "injuries": {"sport": "NFL", "count": 0, "injuries": []},
        "news": {
            "sport": "NFL",
            "count": 1,
            "items": [
                {
                    "player_id": 101,
                    "title": "A bounded update",
                    "created": "2026-09-01 15:00:00",
                    "categories": ["News"],
                }
            ],
        },
        "players:101": {
            "sport": "NFL",
            "players": [
                {
                    "player_id": 101,
                    "player_name": "Jordan Alpha",
                    "position_id": "RB",
                    "team_id": "SF",
                }
            ],
        },
    }
    identity = [{"name": "Jordan Alpha", "position": "RB", "team": "SF"}]
    first_transport = FakeTransport(payloads)
    await _provider(
        api_key="secret",
        transport=first_transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)
    assert len(first_transport.calls) == 4
    with sqlite3.connect(snapshot_cache_module.DEFAULT_SNAPSHOT_CACHE_PATH) as connection:
        player_variants = connection.execute(
            "SELECT variant FROM snapshots WHERE endpoint = 'players'"
        ).fetchall()
    assert player_variants == [("catalog",)]

    current[0] += timedelta(minutes=6)
    payloads["news"] = RuntimeError("refresh unavailable")
    restarted_transport = FakeTransport(payloads)
    result = await _provider(
        api_key="secret",
        transport=restarted_transport,
        clock=lambda: current[0],
    ).get_player_updates(identity, year=2026)

    assert [call["url"].rsplit("/", 1)[-1] for call in restarted_transport.calls] == [
        "injuries",
        "news",
    ]
    assert result["coverage"]["news"]["stale"] is True
    assert result["coverage"]["targetedPlayerLookups"]["attempted"] == 0
    assert result["players"][0]["identityResolved"] is False
    assert result["players"][0]["recentNews"] == []
