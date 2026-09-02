"""Tests for the optional, privacy-minimized Databricks advisory critic."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.databricks_advisory_critic import (
    DatabricksAdvisoryConfig,
    DatabricksAdvisoryCritic,
    DatabricksAdvisoryRequest,
)


class FakeResponses:
    def __init__(self, output: str | Exception | Callable[[], str]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        output = self.output
        if isinstance(output, Exception):
            raise output
        if callable(output):
            output = output()
        return SimpleNamespace(output_text=output)


class FakeClient:
    def __init__(self, output: str | Exception | Callable[[], str]) -> None:
        self.responses = FakeResponses(output)


def _recommendation_result(*, position: str = "WR", score: float = 88.5) -> dict[str, Any]:
    return {
        "leagueId": "private-league-id",
        "leagueKey": "private-league-key",
        "generatedAt": "2026-09-01T16:00:00Z",
        "recommendations": [
            {
                "player": {
                    "name": "Private Player Name",
                    "position": position,
                    "team": "PRIVATE-NFL-TEAM",
                },
                "overallScore": score,
                "scores": {
                    "value": 91.2,
                    "rosterConstruction": 84,
                    "draftDynamics": 52,
                    "opponentModel": 73.4,
                    "riskNews": None,
                    "scenario": 65,
                    "arbitrarySecretScore": 100,
                },
                "returnProbability": 0.2719,
                "reasoning": ["raw private context https://private.invalid/?token=secret"],
                "risk": {
                    "status": "questionable",
                    "recentNews": [
                        {
                            "headline": "private headline token=secret",
                            "url": "https://private.invalid/news",
                        }
                    ],
                },
            },
            {
                "player": {
                    "name": "Another Private Player",
                    "position": "RB",
                    "team": "OTHER-PRIVATE-TEAM",
                },
                "overallScore": 81.0,
                "scores": {
                    "value": 80,
                    "rosterConstruction": 75,
                    "draftDynamics": 60,
                    "opponentModel": 72,
                    "riskNews": 82,
                    "scenario": 69,
                },
                "returnProbability": 0.45,
                "risk": {"status": "healthy"},
            },
        ],
        "state": {
            "sessionKey": "private-session-key",
            "currentOverallPick": 17,
            "nextUserPick": 24,
            "userRoster": [
                {
                    "player": "Private Roster Player",
                    "position": "RB",
                    "fantasyTeam": "Private Fantasy Team",
                    "manager": "Private Manager",
                },
                {"player": "Other Private Player", "position": "WR"},
            ],
            "picks": [
                {
                    "pickNumber": 15,
                    "player": "Private Drafted Player",
                    "position": "QB",
                    "fantasyTeam": "Opponent Private Team",
                },
                {
                    "pickNumber": 16,
                    "player": "Other Drafted Player",
                    "position": "WR",
                },
            ],
            "health": {"fresh": False, "teamCountSource": "ledger"},
        },
        "critic": {
            "checks": {
                "allDraftedPlayersResolved": False,
                "stateFresh": False,
                "unexpectedPrivateCheck": "secret",
            }
        },
        "capabilities": {"injuryStatus": False, "externalNews": False},
        "warnings": ["private warning with api_key=secret"],
        "arbitraryBrowserContext": "https://private.invalid/draft?cookie=secret",
    }


def _config(**changes: Any) -> DatabricksAdvisoryConfig:
    config = DatabricksAdvisoryConfig(
        enabled=True,
        host="https://unit-test.cloud.databricks.com",
        model="unit-test-fast-model",
        timeout_seconds=0.5,
        cache_ttl_seconds=30.0,
        cache_max_entries=8,
    )
    return replace(config, **changes)


def _request(*, position: str = "WR", score: float = 88.5) -> DatabricksAdvisoryRequest:
    return DatabricksAdvisoryRequest.from_recommendation(
        _recommendation_result(position=position, score=score)
    )


@pytest.mark.asyncio
async def test_disabled_by_default_never_constructs_a_client() -> None:
    calls = 0

    def client_factory(_host: str) -> FakeClient:
        nonlocal calls
        calls += 1
        raise AssertionError("disabled critic must stay offline")

    result = await DatabricksAdvisoryCritic(client_factory=client_factory).critique(_request())

    assert calls == 0
    assert result.to_dict() == {
        "status": "unavailable",
        "provider": "Databricks",
        "model": None,
        "advisoryOnly": True,
        "cached": False,
        "latencyMs": 0.0,
        "unavailableReason": {
            "code": "disabled",
            "message": "Databricks advisory critic is disabled.",
        },
    }


def test_from_env_uses_only_namespaced_noncredential_configuration() -> None:
    config = DatabricksAdvisoryConfig.from_env(
        {
            "FANTASY_FOOTBALL_DATABRICKS_ADVISORY_ENABLED": "true",
            "FANTASY_FOOTBALL_DATABRICKS_HOST": "https://example.cloud.databricks.com",
            "FANTASY_FOOTBALL_DATABRICKS_MODEL": "fast-endpoint-v2",
            "FANTASY_FOOTBALL_DATABRICKS_ADVISORY_TIMEOUT_SECONDS": "1.25",
            "DATABRICKS_TOKEN": "must-not-be-read-into-config",
        }
    )

    assert config.enabled is True
    assert config.host == "https://example.cloud.databricks.com"
    assert config.model == "fast-endpoint-v2"
    assert config.timeout_seconds == 1.25
    assert "must-not-be-read-into-config" not in repr(config)
    assert not hasattr(config, "token")


def test_default_timeout_matches_the_bounded_luna_canary_budget() -> None:
    config = DatabricksAdvisoryConfig()
    critic = DatabricksAdvisoryCritic(config)

    assert config.timeout_seconds == 8.0
    assert critic.timeout_seconds == 8.0


def test_roster_slot_capability_is_reduced_to_a_fixed_quality_flag() -> None:
    recommendation = _recommendation_result()
    recommendation["capabilities"]["rosterSlotsAvailable"] = False

    request = DatabricksAdvisoryRequest.from_recommendation(recommendation)

    assert "roster_slots_unavailable" in request.quality_flags
    assert "rosterSlotsAvailable" not in repr(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "model"),
    [
        ("http://workspace.cloud.databricks.com", "fast-model"),
        ("https://user:secret@workspace.cloud.databricks.com", "fast-model"),
        ("https://example.invalid", "fast-model"),
        ("https://workspace.cloud.databricks.com/path?token=secret", "fast-model"),
        ("https://workspace.cloud.databricks.com", "model with spaces"),
        ("https://workspace.cloud.databricks.com", "x" * 129),
    ],
)
async def test_invalid_host_or_model_fails_open_without_a_client(
    host: str, model: str
) -> None:
    result = await DatabricksAdvisoryCritic(
        _config(host=host, model=model),
        client_factory=lambda _host: (_ for _ in ()).throw(AssertionError("must not call")),
    ).critique(_request())

    data = result.to_dict()
    assert data["status"] == "unavailable"
    assert data["unavailableReason"]["code"] == "invalid_config"
    assert "secret" not in repr(data)


@pytest.mark.asyncio
async def test_prompt_is_allowlisted_and_sdk_call_omits_temperature() -> None:
    client = FakeClient(
        json.dumps(
            {
                "summary": "The immutable top-ranked profile is supported by the score mix.",
                "cautions": ["Injury data is unavailable."],
            }
        )
    )
    result = await DatabricksAdvisoryCritic(
        _config(), client_factory=lambda _host: client
    ).critique(_request())

    assert result.status == "available"
    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["model"] == "unit-test-fast-model"
    assert call["max_output_tokens"] == 256
    assert call["timeout"] == 0.5
    assert call["reasoning"] == {"effort": "low"}
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "draft_advisory",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 240,
                    },
                    "cautions": {
                        "type": "array",
                        "maxItems": 2,
                        "items": {"type": "string", "maxLength": 240},
                    },
                },
                "required": ["summary", "cautions"],
                "additionalProperties": False,
            },
        }
    }
    assert "temperature" not in call
    assert set(call) == {
        "model",
        "input",
        "max_output_tokens",
        "timeout",
        "reasoning",
        "text",
    }

    prompt = call["input"]
    payload = json.loads(prompt.rsplit("\n", 1)[-1])
    assert payload == {
        "candidates": [
            {
                "draftDynamicsScore": 52.0,
                "opponentModelScore": 73.4,
                "ordinal": 1,
                "overallScore": 88.5,
                "position": "WR",
                "returnProbability": 0.2719,
                "riskNewsScore": None,
                "riskStatus": "unknown",
                "rosterConstructionScore": 84.0,
                "scenarioScore": 65.0,
                "valueScore": 91.2,
            },
            {
                "draftDynamicsScore": 60.0,
                "opponentModelScore": 72.0,
                "ordinal": 2,
                "overallScore": 81.0,
                "position": "RB",
                "returnProbability": 0.45,
                "riskNewsScore": None,
                "riskStatus": "unknown",
                "rosterConstructionScore": 75.0,
                "scenarioScore": 69.0,
                "valueScore": 80.0,
            },
        ],
        "currentOverallPick": 17,
        "nextUserPick": 24,
        "probabilityCalibration": "uncalibrated",
        "qualityFlags": [
            "external_news_unavailable",
            "inferred_team_count",
            "injury_status_unavailable",
            "state_stale",
            "unresolved_drafted_players",
        ],
        "recentPickPositions": ["QB", "WR"],
        "rosterPositionCounts": {"RB": 1, "WR": 1},
        "schemaVersion": 1,
    }
    forbidden = (
        "private-league",
        "private-session",
        "private player",
        "private roster",
        "private fantasy",
        "private manager",
        "private drafted",
        "private-nfl-team",
        "https://",
        "token=",
        "api_key",
        "headline",
        "warning",
    )
    assert all(value not in prompt.casefold() for value in forbidden)


@pytest.mark.asyncio
async def test_response_is_strictly_parsed_sanitized_and_bounded() -> None:
    long_summary = "A" * 700 + " https://private.invalid/?token=secret\x00"
    long_caution = "B" * 400 + " password=secret"
    client = FakeClient(
        json.dumps(
            {
                "summary": long_summary,
                "cautions": [long_caution, "Second\n caution", 17, "", "ignored fifth"],
            }
        )
    )
    result = await DatabricksAdvisoryCritic(
        _config(), client_factory=lambda _host: client
    ).critique(_request())

    data = result.to_dict()
    assert data["status"] == "available"
    assert data["provider"] == "Databricks"
    assert data["model"] == "unit-test-fast-model"
    assert data["advisoryOnly"] is True
    assert len(data["summary"]) <= 240
    assert len(data["cautions"]) == 2
    assert all(len(item) <= 240 for item in data["cautions"])
    assert "https://" not in repr(data)
    assert "secret" not in repr(data).casefold()
    assert "\x00" not in repr(data)
    assert "preferredCandidate" not in data
    assert "confidence" not in data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "not json",
        "[]",
        '{"summary":"ok","cautions":[],"preferredCandidate":2}',
        '{"summary":"","cautions":[]}',
        '{"summary":"ok","cautions":"not-a-list"}',
        "{" + '"summary":"' + ("x" * 9000) + '","cautions":[]}',
    ],
)
async def test_invalid_or_order_mutating_response_fails_open(output: str) -> None:
    client = FakeClient(output)
    result = await DatabricksAdvisoryCritic(
        _config(), client_factory=lambda _host: client
    ).critique(_request())

    data = result.to_dict()
    assert data["status"] == "unavailable"
    assert data["unavailableReason"] == {
        "code": "invalid_response",
        "message": "Databricks returned an unusable advisory response.",
    }
    assert "summary" not in data
    assert "cautions" not in data


@pytest.mark.asyncio
async def test_provider_errors_fail_open_without_exposing_exception_text() -> None:
    client = FakeClient(RuntimeError("authorization=Bearer secret-token"))
    result = await DatabricksAdvisoryCritic(
        _config(), client_factory=lambda _host: client
    ).critique(_request())

    data = result.to_dict()
    assert data["status"] == "unavailable"
    assert data["unavailableReason"]["code"] == "provider_error"
    assert "secret-token" not in repr(data)


@pytest.mark.asyncio
async def test_missing_optional_dependencies_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.services.databricks_advisory_critic as module

    def missing_import(_name: str) -> Any:
        raise ModuleNotFoundError("optional package missing")

    monkeypatch.setattr(module.importlib, "import_module", missing_import)
    result = await DatabricksAdvisoryCritic(_config()).critique(_request())

    assert result.to_dict()["unavailableReason"] == {
        "code": "dependency_missing",
        "message": "Optional Databricks advisory dependencies are not installed.",
    }


@pytest.mark.asyncio
async def test_sync_sdk_call_runs_off_loop_and_has_an_outer_timeout() -> None:
    release = threading.Event()

    def blocked_output() -> str:
        release.wait(1.0)
        return '{"summary":"late","cautions":[]}'

    client = FakeClient(blocked_output)
    critic = DatabricksAdvisoryCritic(
        _config(timeout_seconds=0.02), client_factory=lambda _host: client
    )
    loop_advanced = asyncio.Event()

    async def mark_loop() -> None:
        await asyncio.sleep(0)
        loop_advanced.set()

    marker = asyncio.create_task(mark_loop())
    try:
        result = await critic.critique(_request())
    finally:
        release.set()
        await marker

    assert loop_advanced.is_set()
    assert result.to_dict()["unavailableReason"]["code"] == "timeout"


@pytest.mark.asyncio
async def test_available_results_use_ttl_lru_cache_keyed_by_sanitized_request() -> None:
    now = 100.0
    clients: list[FakeClient] = []

    def clock() -> float:
        return now

    def client_factory(_host: str) -> FakeClient:
        client = FakeClient('{"summary":"supported","cautions":[]}')
        clients.append(client)
        return client

    critic = DatabricksAdvisoryCritic(
        _config(cache_max_entries=1, cache_ttl_seconds=10),
        client_factory=client_factory,
        clock=clock,
    )
    first = await critic.critique(_request(position="WR"))
    cached = await critic.critique(_request(position="WR"))
    different = await critic.critique(_request(position="RB"))
    evicted = await critic.critique(_request(position="WR"))
    now += 11
    expired = await critic.critique(_request(position="WR"))

    assert first.cached is False
    assert cached.cached is True
    assert different.cached is False
    assert evicted.cached is False
    assert expired.cached is False
    assert len(clients) == 1
    assert len(clients[0].responses.calls) == 4


@pytest.mark.asyncio
async def test_cache_key_includes_fixed_prompt_and_response_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.databricks_advisory_critic as module

    client = FakeClient('{"summary":"supported","cautions":[]}')
    critic = DatabricksAdvisoryCritic(_config(), client_factory=lambda _host: client)

    first = await critic.critique(_request())
    monkeypatch.setattr(module, "_PROMPT_PREFIX", module._PROMPT_PREFIX + " Revised.")
    changed_prompt = await critic.critique(_request())
    original_text_config = module._response_text_config

    def revised_text_config() -> dict[str, Any]:
        config = original_text_config()
        config["format"]["name"] = "revised_draft_advisory"
        return config

    monkeypatch.setattr(module, "_response_text_config", revised_text_config)
    changed_schema = await critic.critique(_request())

    assert first.cached is False
    assert changed_prompt.cached is False
    assert changed_schema.cached is False
    assert len(client.responses.calls) == 3


@pytest.mark.asyncio
async def test_concurrent_identical_calls_are_coalesced() -> None:
    started = threading.Event()
    release = threading.Event()
    client = FakeClient(
        lambda: (
            started.set(),
            release.wait(1),
            '{"summary":"coalesced","cautions":[]}',
        )[-1]
    )
    critic = DatabricksAdvisoryCritic(_config(), client_factory=lambda _host: client)

    first_task = asyncio.create_task(critic.critique(_request()))
    await asyncio.to_thread(started.wait, 1)
    second_task = asyncio.create_task(critic.critique(_request()))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert len(client.responses.calls) == 1
    assert first.status == second.status == "available"
    assert {first.cached, second.cached} == {False, True}


@pytest.mark.asyncio
async def test_distinct_concurrency_is_bounded_and_canceled_waiters_are_cleaned_up() -> None:
    class TrackingResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.release = threading.Event()
            self.two_active = threading.Event()
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def create(self, **kwargs: Any) -> SimpleNamespace:
            with self.lock:
                self.calls.append(dict(kwargs))
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.active == 2:
                    self.two_active.set()
            try:
                self.release.wait(2.0)
                return SimpleNamespace(
                    output_text='{"summary":"bounded","cautions":[]}'
                )
            finally:
                with self.lock:
                    self.active -= 1

    responses = TrackingResponses()
    client = SimpleNamespace(responses=responses)
    critic = DatabricksAdvisoryCritic(
        _config(timeout_seconds=5.0), client_factory=lambda _host: client
    )
    tasks = [
        asyncio.create_task(critic.critique(_request(score=80.0 + index)))
        for index in range(10)
    ]

    assert await asyncio.to_thread(responses.two_active.wait, 1.0)
    await asyncio.sleep(0)
    assert len(critic._inflight) <= 4
    excess = await asyncio.wait_for(asyncio.gather(*tasks[4:]), timeout=0.2)
    assert all(result.status == "unavailable" for result in excess)
    assert all(
        result.unavailable_reason is not None
        and result.unavailable_reason.code == "provider_error"
        for result in excess
    )

    tasks[0].cancel()
    tasks[2].cancel()
    canceled = await asyncio.gather(tasks[0], tasks[2], return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in canceled)

    responses.release.set()
    admitted = await asyncio.gather(tasks[1], tasks[3])
    assert all(result.status == "available" for result in admitted)
    for _ in range(20):
        if not critic._inflight:
            break
        await asyncio.sleep(0)

    assert len(responses.calls) == 4
    assert responses.max_active <= 2
    assert critic._inflight == {}
