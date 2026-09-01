"""Tests for wiring live state and Yahoo context into the specialist suite."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

import src.services.live_draft_recommendation_service as recommendation_service
from src.agents.live_draft_recommender import LiveDraftRecommendationEngine
from src.services.live_draft_recommendation_service import get_live_draft_recommendation
from src.services.live_draft_store import save_live_draft
from tests.unit.test_live_draft_recommender import live_context, rankings


def local_profile() -> dict:
    return {
        "schemaVersion": 1,
        "source": "local-draft-profile",
        "season": 2026,
        "importedAt": "2026-09-01T16:00:00Z",
        "draft": dict(live_context()["draft"]),
        "rankings": [
            {
                key: value
                for key, value in player.items()
                if key in {
                    "name",
                    "position",
                    "team",
                    "rank",
                    "average_draft_position",
                    "bye_week",
                    "bye",
                }
            }
            for player in rankings()
        ],
        "leagueSettings": {
            "teams": 12,
            "rosterPositions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "FLEX", "count": 1},
                {"position": "K", "count": 1},
                {"position": "DST", "count": 1},
                {"position": "BN", "count": 6},
                {"position": "IR", "count": 1},
            ],
        },
        "provenance": {
            "kind": "user-import",
            "format": "draftsheets-2026",
            "asOf": "2026-09-01",
        },
    }


class FakeFantasyProsProvider:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = []

    async def get_player_updates(self, players, **arguments):
        self.calls.append((deepcopy(list(players)), dict(arguments)))
        return deepcopy(self.result)


def fantasypros_result(profile: dict, *, status: str = "success") -> dict:
    updates = []
    for index, ranking in enumerate(profile["rankings"], start=1):
        update = {
            "name": ranking["name"],
            "position": ranking["position"],
            "team": ranking.get("team", ""),
            "fantasypros_id": index,
            "identityResolved": True,
            "injury_status": "unknown",
            "injury_source": None,
            "injury_updated_at": None,
            "injury_fresh": False,
            "news_source": None,
            "news_updated_at": None,
            "news_fresh": False,
            "recentNews": [],
            "retrievedAt": "2026-09-01T17:00:00Z",
        }
        if ranking["name"] == "De'Von Achane":
            update.update(
                {
                    "injury_status": "questionable",
                    "injury_source": "FantasyPros",
                    "injury_updated_at": "2026-09-01T16:30:00Z",
                    "injury_fresh": True,
                }
            )
        if ranking["name"] == "CeeDee Lamb":
            update.update(
                {
                    "news_source": "FantasyPros",
                    "news_updated_at": "2026-09-01T16:45:00Z",
                    "news_fresh": True,
                    "recentNews": [
                        {
                            "headline": "Returns to full team drills",
                            "category": "injury",
                            "publishedAt": "2026-09-01T16:45:00Z",
                        }
                    ],
                }
            )
        updates.append(update)
    return {
        "status": status,
        "provider": "FantasyPros",
        "retrievedAt": "2026-09-01T17:00:00Z",
        "players": updates,
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_local_profile_avoids_yahoo_and_adds_fantasypros_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    profile = local_profile()
    profile_path = tmp_path / "profiles.json"
    loads = []

    def load_profile(identity, path=None):
        loads.append((dict(identity), path))
        return deepcopy(profile)

    monkeypatch.setattr(recommendation_service, "load_local_draft_profile", load_profile)
    provider = FakeFantasyProsProvider(fantasypros_result(profile))

    async def no_yahoo(name: str, **arguments):
        raise AssertionError(f"Yahoo must not be called when a local profile exists: {name}")

    result = await get_live_draft_recommendation(
        no_yahoo,
        league_key=None,
        league_id="10462193",
        count=6,
        simulations=8,
        store_path=path,
        profile_path=profile_path,
        fantasypros_provider=provider,
        require_authenticated_team=True,
    )

    assert result["status"] == "success"
    assert result["leagueId"] == "10462193"
    assert result["leagueKey"] is None
    assert result["state"]["teamCount"] == 12
    assert result["state"]["health"]["teamCountSource"] == "league"
    assert result["capabilities"] == {
        "externalNews": True,
        "injuryStatus": True,
        "opponentModel": "heuristic",
        "scenarioSimulation": True,
        "llmOnRequestPath": False,
    }
    assert result["dataSources"] == {
        "liveState": "local browser extension",
        "rankings": "user-imported DraftSheets 2026",
        "league": "user-imported league profile",
        "injuryNews": "FantasyPros public API",
    }
    assert result["enrichment"]["status"] == "success"
    assert result["enrichment"]["freshInjuryPlayers"] == 1
    assert result["enrichment"]["freshNewsPlayers"] == 1
    assert len(loads) == 2
    assert all(call[0] == live_context()["draft"] for call in loads)
    assert all(call[1] == profile_path for call in loads)
    assert provider.calls[0][1] == {"year": 2026, "week": 0}
    assert len(provider.calls[0][0]) == len(profile["rankings"])
    lamb = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert lamb["risk"]["recentNews"][0]["headline"] == "Returns to full team drills"
    achane = next(
        item for item in result["recommendations"] if item["player"]["name"] == "De'Von Achane"
    )
    assert achane["risk"]["status"] == "questionable"


@pytest.mark.asyncio
async def test_unavailable_fantasypros_degrades_without_exposing_exception(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    profile = local_profile()
    monkeypatch.setattr(
        recommendation_service,
        "load_local_draft_profile",
        lambda identity, path=None: deepcopy(profile),
    )

    class FailingProvider:
        async def get_player_updates(self, players, **arguments):
            raise RuntimeError("x-api-key=secret-that-must-not-escape")

    async def no_yahoo(name: str, **arguments):
        raise AssertionError(name)

    result = await get_live_draft_recommendation(
        no_yahoo,
        league_key=None,
        league_id="10462193",
        store_path=path,
        profile_path=tmp_path / "profiles.json",
        fantasypros_provider=FailingProvider(),
        simulations=0,
    )

    assert result["status"] == "degraded"
    assert result["capabilities"]["injuryStatus"] is False
    assert result["capabilities"]["externalNews"] is False
    assert result["enrichment"]["status"] == "unavailable"
    assert any("FantasyPros" in warning for warning in result["warnings"])
    assert "secret-that-must-not-escape" not in repr(result)


@pytest.mark.asyncio
async def test_service_discards_result_when_local_profile_changes_during_scoring(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    profile = local_profile()
    changed = deepcopy(profile)
    changed["importedAt"] = "2026-09-01T17:00:01Z"
    calls = 0

    def load_profile(identity, path=None):
        nonlocal calls
        calls += 1
        return deepcopy(profile if calls == 1 else changed)

    monkeypatch.setattr(recommendation_service, "load_local_draft_profile", load_profile)
    provider = FakeFantasyProsProvider(fantasypros_result(profile))

    async def no_yahoo(name: str, **arguments):
        raise AssertionError(name)

    result = await get_live_draft_recommendation(
        no_yahoo,
        league_key=None,
        league_id="10462193",
        store_path=path,
        profile_path=tmp_path / "profiles.json",
        fantasypros_provider=provider,
        simulations=0,
    )

    assert result["status"] == "error"
    assert result["errorCode"] == "draft_profile_changed"
    assert result["refreshRequired"] is True
    assert result["recommendations"] == []


@pytest.mark.asyncio
async def test_service_combines_local_state_with_yahoo_rankings(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    calls = []

    async def call_tool(name: str, **arguments):
        calls.append((name, arguments))
        if name == "ff_get_draft_rankings":
            return {"rankings": rankings()}
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "scoring_type": "head",
                "roster_positions": [{"position": "QB", "count": 1}],
            }
        raise AssertionError(name)

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        strategy="balanced",
        count=3,
        ranking_count=200,
        simulations=32,
        store_path=path,
    )

    assert result["status"] == "degraded"
    assert result["leagueId"] == "10462193"
    assert len(result["recommendations"]) == 3
    assert {name for name, _ in calls} == {"ff_get_draft_rankings", "ff_get_league_info"}
    ranking_call = next(arguments for name, arguments in calls if name == "ff_get_draft_rankings")
    assert ranking_call["count"] == 200


@pytest.mark.asyncio
async def test_service_bounds_yahoo_rankings_even_when_upstream_ignores_count(
    tmp_path: Path,
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    upstream_rankings = [
        {
            "name": f"Candidate {index}",
            "position": "WR",
            "team": "SF",
            "rank": index,
            "average_draft_position": float(index),
        }
        for index in range(1, 1_001)
    ]
    provider = FakeFantasyProsProvider(
        {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": [],
        }
    )

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "roster_positions": [{"position": "QB", "count": 1}],
            }
        if name == "ff_get_draft_rankings":
            assert arguments["count"] == 25
            return {"rankings": upstream_rankings}
        raise AssertionError(name)

    await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        ranking_count=25,
        simulations=0,
        store_path=path,
        fantasypros_provider=provider,
    )

    assert len(provider.calls) == 1
    assert len(provider.calls[0][0]) == 25
    assert [item["rank"] for item in provider.calls[0][0]] == list(range(1, 26))


@pytest.mark.asyncio
async def test_service_rejects_mismatched_league_identifiers(tmp_path: Path) -> None:
    async def call_tool(name: str, **arguments):
        raise AssertionError("Yahoo should not be called for mismatched league identifiers")

    with pytest.raises(ValueError, match="match"):
        await get_live_draft_recommendation(
            call_tool,
            league_key="nfl.l.10462193",
            league_id="999",
            store_path=tmp_path / "drafts.json",
        )


@pytest.mark.asyncio
async def test_service_returns_actionable_error_without_synced_state(tmp_path: Path) -> None:
    async def call_tool(name: str, **arguments):
        raise AssertionError("Yahoo should not be called without local state")

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        league_id="10462193",
        store_path=tmp_path / "missing.json",
    )

    assert result["status"] == "error"
    assert "extension" in result["message"].lower()


@pytest.mark.asyncio
async def test_service_preserves_yahoo_ranking_error(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {"teams": 4}
        return {"status": "error", "message": "Yahoo ranking quota exceeded"}

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        league_id="10462193",
        store_path=path,
    )

    assert result["status"] == "degraded"
    assert any("quota exceeded" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_service_degrades_when_roster_slots_are_missing(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {"teams": 4}
        return {"rankings": rankings()}

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        store_path=path,
    )

    assert result["status"] == "degraded"
    assert any("roster positions" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_service_preserves_league_info_error(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {"error": "League settings unavailable"}
        return {"rankings": rankings()}

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="nfl.l.10462193",
        league_id="10462193",
        store_path=path,
    )

    assert result["status"] == "degraded"
    assert any("settings unavailable" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_service_resolves_exact_yahoo_league_key_from_private_state(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    calls = []

    async def call_tool(name: str, **arguments):
        calls.append((name, arguments))
        if name == "ff_get_leagues":
            return {
                "leagues": [
                    {"key": "461.l.999", "name": "Other"},
                    {"key": "461.l.10462193", "name": "Draft league"},
                ]
            }
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "roster_positions": [{"position": "QB", "count": 1}],
                "your_team": {"key": "461.l.10462193.t.6"},
            }
        return {"rankings": rankings()}

    result = await get_live_draft_recommendation(
        call_tool,
        league_key=None,
        league_id="10462193",
        store_path=path,
        simulations=0,
    )

    assert result["status"] == "degraded"
    assert result["leagueKey"] == "461.l.10462193"
    assert [name for name, _ in calls] == [
        "ff_get_leagues",
        "ff_get_league_info",
        "ff_get_draft_rankings",
    ]
    assert calls[1][1]["league_key"] == "461.l.10462193"
    assert calls[2][1]["league_key"] == "461.l.10462193"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "league_keys, message",
    [
        (["461.l.999"], "could not be resolved"),
        (["461.l.10462193", "462.l.10462193"], "ambiguous"),
    ],
)
async def test_service_rejects_unresolved_or_ambiguous_yahoo_league_identity(
    tmp_path: Path, league_keys: list[str], message: str
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)

    async def call_tool(name: str, **arguments):
        assert name == "ff_get_leagues"
        return {"leagues": [{"key": key} for key in league_keys]}

    with pytest.raises(ValueError, match=message):
        await get_live_draft_recommendation(
            call_tool,
            league_key=None,
            league_id="10462193",
            store_path=path,
        )


@pytest.mark.asyncio
async def test_service_rejects_authenticated_team_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "roster_positions": [{"position": "QB", "count": 1}],
                "your_team": {"key": "461.l.10462193.t.999"},
            }
        return {"rankings": rankings()}

    with pytest.raises(ValueError, match="team identity"):
        await get_live_draft_recommendation(
            call_tool,
            league_key="461.l.10462193",
            league_id="10462193",
            store_path=path,
            require_authenticated_team=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "league_info",
    [
        {"teams": 4, "roster_positions": [{"position": "QB", "count": 1}]},
        {
            "teams": 4,
            "roster_positions": [{"position": "QB", "count": 1}],
            "your_team": {"key": "malformed"},
        },
    ],
)
async def test_ui_service_requires_verified_authenticated_team(
    tmp_path: Path, league_info: dict
) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    calls = []

    async def call_tool(name: str, **arguments):
        calls.append(name)
        if name == "ff_get_league_info":
            return league_info
        raise AssertionError("rankings must not be called before team identity is verified")

    with pytest.raises(ValueError, match="team identity"):
        await get_live_draft_recommendation(
            call_tool,
            league_key="461.l.10462193",
            league_id="10462193",
            store_path=path,
            require_authenticated_team=True,
        )

    assert calls == ["ff_get_league_info"]


@pytest.mark.asyncio
async def test_service_serializes_all_yahoo_calls_across_ui_requests(tmp_path: Path) -> None:
    path = tmp_path / "drafts.json"
    save_live_draft(live_context(), path)
    events: list[str] = []

    def caller(label: str):
        async def call_tool(name: str, **arguments):
            events.append(f"{label}:{name}:start")
            await asyncio.sleep(0.01)
            events.append(f"{label}:{name}:end")
            if name == "ff_get_league_info":
                return {
                    "teams": 4,
                    "roster_positions": [{"position": "QB", "count": 1}],
                }
            return {"rankings": rankings()}

        return call_tool

    await asyncio.gather(
        get_live_draft_recommendation(
            caller("a"),
            league_key="461.l.10462193",
            store_path=path,
            simulations=0,
        ),
        get_live_draft_recommendation(
            caller("b"),
            league_key="461.l.10462193",
            store_path=path,
            simulations=0,
        ),
    )

    assert events in (
        [
            "a:ff_get_league_info:start",
            "a:ff_get_league_info:end",
            "a:ff_get_draft_rankings:start",
            "a:ff_get_draft_rankings:end",
            "b:ff_get_league_info:start",
            "b:ff_get_league_info:end",
            "b:ff_get_draft_rankings:start",
            "b:ff_get_draft_rankings:end",
        ],
        [
            "b:ff_get_league_info:start",
            "b:ff_get_league_info:end",
            "b:ff_get_draft_rankings:start",
            "b:ff_get_draft_rankings:end",
            "a:ff_get_league_info:start",
            "a:ff_get_league_info:end",
            "a:ff_get_draft_rankings:start",
            "a:ff_get_draft_rankings:end",
        ],
    )


@pytest.mark.asyncio
async def test_service_discards_result_when_synced_snapshot_advances_during_scoring(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drafts.json"
    initial = live_context()
    initial["generatedAt"] = "2026-09-01T12:00:00+00:00"
    save_live_draft(initial, path)
    advanced = deepcopy(initial)
    advanced["generatedAt"] = "2026-09-01T12:00:01+00:00"
    advanced["picks"].append(
        {
            "pickNumber": 7,
            "player": "A. St. Brown",
            "position": "WR",
            "nflTeam": "DET",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        }
    )

    original_recommend = LiveDraftRecommendationEngine.recommend

    def recommend_then_sync(self, *args, **kwargs):
        result = original_recommend(self, *args, **kwargs)
        save_live_draft(advanced, path)
        return result

    monkeypatch.setattr(
        LiveDraftRecommendationEngine,
        "recommend",
        recommend_then_sync,
    )

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "roster_positions": [{"position": "QB", "count": 1}],
            }
        return {"rankings": rankings()}

    result = await get_live_draft_recommendation(
        call_tool,
        league_key="461.l.10462193",
        league_id="10462193",
        store_path=path,
        simulations=0,
    )

    assert result == {
        "status": "error",
        "errorCode": "draft_state_changed",
        "refreshRequired": True,
        "message": (
            "The synced draft changed while recommendations were being computed. "
            "Refresh recommendations to analyze the latest picks."
        ),
        "leagueId": "10462193",
        "primaryRecommendation": None,
        "alternatives": [],
        "recommendations": [],
        "contingency": None,
    }


@pytest.mark.asyncio
async def test_service_revalidation_rejects_new_same_league_session_ambiguity(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "drafts.json"
    initial = live_context()
    initial["generatedAt"] = "2026-09-01T12:00:00+00:00"
    save_live_draft(initial, path)
    ambiguous = deepcopy(initial)
    ambiguous["generatedAt"] = "2026-09-01T12:00:01+00:00"
    ambiguous["draft"]["sport"] = "football"
    ambiguous["draft"]["sessionKey"] = "football:10462193"

    original_recommend = LiveDraftRecommendationEngine.recommend

    def recommend_then_add_ambiguous_session(self, *args, **kwargs):
        result = original_recommend(self, *args, **kwargs)
        save_live_draft(ambiguous, path)
        return result

    monkeypatch.setattr(
        LiveDraftRecommendationEngine,
        "recommend",
        recommend_then_add_ambiguous_session,
    )

    async def call_tool(name: str, **arguments):
        if name == "ff_get_league_info":
            return {
                "teams": 4,
                "roster_positions": [{"position": "QB", "count": 1}],
            }
        return {"rankings": rankings()}

    with pytest.raises(ValueError, match="ambiguous across stored sessions"):
        await get_live_draft_recommendation(
            call_tool,
            league_key="461.l.10462193",
            league_id="10462193",
            store_path=path,
            simulations=0,
        )
