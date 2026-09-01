"""Application service that joins private live state with Yahoo draft data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from src.agents.live_draft_recommender import LiveDraftRecommendationEngine
from src.services.live_draft_store import LiveDraftValidationError, load_live_draft

ToolCaller = Callable[..., Awaitable[dict[str, Any]]]


async def get_live_draft_recommendation(
    call_tool: ToolCaller,
    *,
    league_key: str,
    league_id: str | None = None,
    strategy: str = "balanced",
    count: int = 5,
    ranking_count: int = 250,
    simulations: int = 256,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a recommendation while keeping network calls off the scoring path."""

    marker = ".l."
    if marker not in league_key:
        raise LiveDraftValidationError("league_key must contain a Yahoo .l. league identifier")
    derived_league_id = league_key.rsplit(marker, 1)[1]
    if not derived_league_id:
        raise LiveDraftValidationError("league_key must include a league identifier")
    if league_id is not None and str(league_id) != derived_league_id:
        raise LiveDraftValidationError("league_id must match league_key")
    league_id = derived_league_id

    live_state = load_live_draft(league_id=league_id, path=store_path)
    if live_state is None:
        return {
            "status": "error",
            "message": (
                "No synced live draft state was found. Load the Yahoo Draft Recorder "
                "extension, open Results → Round by Round, and rescan the page."
            ),
            "leagueId": league_id,
        }

    # Keep Yahoo calls serialized: concurrent 401 responses can race rotating token refreshes.
    league_result = await call_tool("ff_get_league_info", league_key=league_key)
    rankings_result = await call_tool(
        "ff_get_draft_rankings",
        league_key=league_key,
        position="all",
        count=max(25, min(int(ranking_count), 500)),
    )
    rankings = rankings_result.get("rankings", []) if isinstance(rankings_result, Mapping) else []
    league_info = dict(league_result) if isinstance(league_result, Mapping) else {}
    engine = LiveDraftRecommendationEngine(simulations=max(0, min(int(simulations), 512)))
    result = await asyncio.to_thread(
        engine.recommend,
        live_state,
        rankings if isinstance(rankings, list) else [],
        league_info,
        strategy=strategy,
        count=max(1, min(int(count), 20)),
    )
    if isinstance(rankings_result, Mapping) and not rankings:
        ranking_error = rankings_result.get("message") or rankings_result.get("error")
        if ranking_error:
            result["warnings"].append(f"Yahoo rankings: {ranking_error}")
    league_warning = None
    if isinstance(league_result, Mapping) and (
        "error" in league_result or league_result.get("status") == "error"
    ):
        league_warning = league_result.get("message") or league_result.get("error")
    elif not isinstance(league_info.get("roster_positions"), list) or not league_info.get(
        "roster_positions"
    ):
        league_warning = "Yahoo league roster positions are unavailable; using 1QB defaults"
    if league_warning:
        result["warnings"].append(f"Yahoo league info: {league_warning}")
        if result.get("status") == "success":
            result["status"] = "degraded"
    result["leagueKey"] = league_key
    result["leagueId"] = live_state.get("draft", {}).get("leagueId")
    result["dataSources"] = {
        "liveState": "local browser extension",
        "rankings": "Yahoo pre-draft rankings",
        "league": "Yahoo league info",
    }
    return result
