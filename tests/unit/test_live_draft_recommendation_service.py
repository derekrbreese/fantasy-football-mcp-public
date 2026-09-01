"""Tests for wiring live state and Yahoo context into the specialist suite."""

from pathlib import Path

import pytest

from src.services.live_draft_recommendation_service import get_live_draft_recommendation
from src.services.live_draft_store import save_live_draft
from tests.unit.test_live_draft_recommender import live_context, rankings


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

    assert result["status"] == "success"
    assert result["leagueId"] == "10462193"
    assert len(result["recommendations"]) == 3
    assert {name for name, _ in calls} == {"ff_get_draft_rankings", "ff_get_league_info"}
    ranking_call = next(arguments for name, arguments in calls if name == "ff_get_draft_rankings")
    assert ranking_call["count"] == 200


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
