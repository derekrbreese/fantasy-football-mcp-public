"""Application service that joins private live state with Yahoo draft data."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from src.agents.live_draft_recommender import LiveDraftRecommendationEngine
from src.services.databricks_advisory_critic import (
    DatabricksAdvisoryCritic,
    DatabricksAdvisoryRequest,
    DatabricksAdvisoryResult,
)
from src.services.fantasypros_provider import FantasyProsProvider
from src.services.live_draft_store import LiveDraftValidationError, load_live_draft
from src.services.local_draft_profile_store import (
    LocalDraftProfileConflictError,
    LocalDraftProfileNotFoundError,
    LocalDraftProfileValidationError,
    bind_default_local_draft_profile,
    load_local_draft_profile,
)

ToolCaller = Callable[..., Awaitable[dict[str, Any]]]
_YAHOO_RECOMMENDATION_LOCK = asyncio.Lock()
_DRAFT_IDENTITY_FIELDS = ("sport", "leagueId", "teamId", "sessionKey")
_FANTASYPROS_PROVIDER = FantasyProsProvider()
_DATABRICKS_ADVISORY_CRITIC = DatabricksAdvisoryCritic()
_FANTASYPROS_FIELDS = (
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
)
_POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST"}


def _advisory_critic_enabled(critic: Any) -> bool:
    try:
        return critic.enabled is True
    except Exception:
        return False


def _advisory_critic_eligible(result: Mapping[str, Any], critic: Any) -> bool:
    if not _advisory_critic_enabled(critic):
        return False
    if result.get("status") not in {"success", "degraded"}:
        return False
    recommendations = result.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        return False
    state = result.get("state")
    health = state.get("health") if isinstance(state, Mapping) else None
    return isinstance(health, Mapping) and health.get("complete") is True


def _advisory_model(critic: Any) -> str | None:
    try:
        model = critic.model
    except Exception:
        return None
    return model if isinstance(model, str) else None


def _advisory_outer_timeout(critic: Any) -> float:
    try:
        timeout = float(critic.timeout_seconds)
    except (AttributeError, TypeError, ValueError, OverflowError):
        timeout = 1.5
    if not 0.01 <= timeout <= 8.0:
        timeout = 8.0
    return min(8.25, timeout + 0.25)


async def _run_advisory_critic(
    result: Mapping[str, Any], critic: Any
) -> DatabricksAdvisoryResult | None:
    if not _advisory_critic_eligible(result, critic):
        return None
    request = DatabricksAdvisoryRequest.from_recommendation(result)
    try:
        advisory = await asyncio.wait_for(
            critic.critique(request),
            timeout=_advisory_outer_timeout(critic),
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return DatabricksAdvisoryResult.unavailable(
            "timeout", model=_advisory_model(critic)
        )
    except Exception:
        return DatabricksAdvisoryResult.unavailable(
            "provider_error", model=_advisory_model(critic)
        )
    if not isinstance(advisory, DatabricksAdvisoryResult):
        return DatabricksAdvisoryResult.unavailable(
            "provider_error", model=_advisory_model(critic)
        )
    return advisory


def _snapshot_binding(live_state: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    draft = live_state.get("draft")
    generated_at = live_state.get("generatedAt")
    if not isinstance(draft, Mapping) or not isinstance(generated_at, str) or not generated_at:
        raise LiveDraftValidationError(
            "synced draft snapshot identity and generatedAt are required"
        )
    identity = tuple(draft.get(field) for field in _DRAFT_IDENTITY_FIELDS)
    if any(not isinstance(value, str) or not value for value in identity):
        raise LiveDraftValidationError(
            "synced draft snapshot identity and generatedAt are required"
        )
    return (*identity, generated_at)


def _refresh_required_result(league_id: str) -> dict[str, Any]:
    return {
        "status": "error",
        "errorCode": "draft_state_changed",
        "refreshRequired": True,
        "message": (
            "The synced draft changed while recommendations were being computed. "
            "Refresh recommendations to analyze the latest picks."
        ),
        "leagueId": league_id,
        "primaryRecommendation": None,
        "alternatives": [],
        "recommendations": [],
        "contingency": None,
    }


def _profile_refresh_required_result(league_id: str) -> dict[str, Any]:
    result = _refresh_required_result(league_id)
    result.update(
        {
            "errorCode": "draft_profile_changed",
            "message": (
                "The local ranking/settings profile changed while recommendations were "
                "being computed. Refresh recommendations to use the latest import."
            ),
        }
    )
    return result


def _player_identity(value: Mapping[str, Any]) -> tuple[str, str, str]:
    raw_name = str(value.get("name") or "")
    normalized = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z0-9]", "", normalized.casefold())
    raw_position = str(value.get("position") or "").strip().upper()
    position = _POSITION_ALIASES.get(raw_position, raw_position)
    team = re.sub(r"[^A-Z0-9]", "", str(value.get("team") or "").upper())
    return name, position, team


def _same_enrichment_identity(
    ranking: Mapping[str, Any], update: Mapping[str, Any]
) -> bool:
    ranking_id = ranking.get("fantasypros_id") or ranking.get("fantasyProsId")
    update_id = update.get("fantasypros_id")
    if (
        isinstance(ranking_id, int)
        and not isinstance(ranking_id, bool)
        and isinstance(update_id, int)
        and not isinstance(update_id, bool)
    ):
        return ranking_id == update_id
    ranking_name, ranking_position, ranking_team = _player_identity(ranking)
    update_name, update_position, update_team = _player_identity(update)
    if ranking_position == update_position == "DST":
        return bool(ranking_team and ranking_team == update_team)
    return bool(
        ranking_name
        and ranking_name == update_name
        and ranking_position
        and ranking_position == update_position
        and ranking_team
        and ranking_team == update_team
    )


def _merge_fantasypros_updates(
    rankings: list[dict[str, Any]], provider_result: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_updates = provider_result.get("players")
    updates = raw_updates if isinstance(raw_updates, list) else []
    merged: list[dict[str, Any]] = []
    resolved = 0
    fresh_injuries = 0
    fresh_news = 0
    for index, ranking in enumerate(rankings):
        candidate = dict(ranking)
        update = updates[index] if index < len(updates) else None
        if isinstance(update, Mapping) and _same_enrichment_identity(candidate, update):
            for field in _FANTASYPROS_FIELDS:
                if field in update:
                    candidate[field] = update[field]
            if update.get("identityResolved") is True:
                resolved += 1
            if update.get("injury_fresh") is True:
                fresh_injuries += 1
            if update.get("news_fresh") is True:
                fresh_news += 1
        merged.append(candidate)
    raw_status = provider_result.get("status")
    status = raw_status if raw_status in {"success", "degraded", "unavailable"} else "unavailable"
    retrieved_at = provider_result.get("retrievedAt")
    return merged, {
        "provider": "FantasyPros",
        "status": status,
        "retrievedAt": retrieved_at if isinstance(retrieved_at, str) else None,
        "requestedPlayers": len(rankings),
        "identityResolvedPlayers": resolved,
        "freshInjuryPlayers": fresh_injuries,
        "freshNewsPlayers": fresh_news,
    }


async def _enrich_with_fantasypros(
    rankings: list[dict[str, Any]],
    *,
    season: int | None,
    provider: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    try:
        raw_result = await provider.get_player_updates(
            rankings,
            year=season,
            week=0,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        raw_result = {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": ["FantasyPros injury and news enrichment is temporarily unavailable"],
        }
    if not isinstance(raw_result, Mapping):
        raw_result = {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": ["FantasyPros injury and news enrichment is temporarily unavailable"],
        }
    merged, summary = _merge_fantasypros_updates(rankings, raw_result)
    warnings: list[str] = []
    raw_warnings = raw_result.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings[:6]:
            if isinstance(warning, str) and warning.startswith("FantasyPros"):
                warnings.append(warning[:240])
    if summary["status"] == "unavailable" and not warnings:
        warnings.append(
            "FantasyPros injury and news enrichment is unavailable; missing data remains unknown"
        )
    return merged, summary, warnings


def _profile_source_label(profile: Mapping[str, Any]) -> str:
    provenance = profile.get("provenance")
    format_name = provenance.get("format") if isinstance(provenance, Mapping) else None
    if format_name == "draftsheets-2026":
        return "user-imported DraftSheets 2026"
    if format_name == "csv":
        return "user-imported rankings CSV"
    return "user-imported rankings profile"


def _resolve_league_key(result: Mapping[str, Any], league_id: str) -> str:
    if result.get("status") == "error" or isinstance(result.get("error"), str):
        raise LiveDraftValidationError(
            "Yahoo league discovery is unavailable. Configure Yahoo credentials or "
            "explicitly bind a saved local profile to this draft."
        )
    leagues = result.get("leagues")
    if not isinstance(leagues, list):
        raise LiveDraftValidationError(
            "Yahoo league identity could not be resolved for the synced draft"
        )
    suffix = f".l.{league_id}"
    matches: list[str] = []
    for league in leagues:
        if not isinstance(league, Mapping):
            continue
        key = league.get("key") or league.get("league_key")
        if isinstance(key, str) and key.endswith(suffix):
            matches.append(key)
    unique_matches = sorted(set(matches))
    if not unique_matches:
        raise LiveDraftValidationError(
            "Yahoo league identity could not be resolved for the synced draft"
        )
    if len(unique_matches) != 1:
        raise LiveDraftValidationError(
            "Yahoo league identity is ambiguous for the synced draft"
        )
    return unique_matches[0]


def _validate_authenticated_team(
    league_result: Mapping[str, Any],
    live_state: Mapping[str, Any],
    league_key: str,
    *,
    required: bool,
) -> None:
    your_team = league_result.get("your_team")
    if not isinstance(your_team, Mapping):
        if required:
            raise LiveDraftValidationError(
                "authenticated Yahoo team identity is unavailable for the synced draft"
            )
        return
    team_key = your_team.get("key") or your_team.get("team_key")
    if team_key in (None, ""):
        if required:
            raise LiveDraftValidationError(
                "authenticated Yahoo team identity is unavailable for the synced draft"
            )
        return
    team_id = live_state.get("draft", {}).get("teamId")
    expected = f"{league_key}.t.{team_id}"
    if not isinstance(team_key, str) or team_key != expected:
        raise LiveDraftValidationError(
            "authenticated Yahoo team identity does not match the synced draft"
        )


async def get_live_draft_recommendation(
    call_tool: ToolCaller,
    *,
    league_key: str | None,
    league_id: str | None = None,
    strategy: str = "balanced",
    count: int = 5,
    ranking_count: int = 250,
    simulations: int = 256,
    store_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    fantasypros_provider: Any | None = None,
    advisory_critic: Any | None = None,
    require_authenticated_team: bool = False,
) -> dict[str, Any]:
    """Return a recommendation while keeping network calls off the scoring path."""

    marker = ".l."
    if league_key is None:
        if not isinstance(league_id, str) or not league_id:
            raise LiveDraftValidationError(
                "league_id is required when resolving the Yahoo league key"
            )
        league_id = str(league_id)
    else:
        if marker not in league_key:
            raise LiveDraftValidationError(
                "league_key must contain a Yahoo .l. league identifier"
            )
        derived_league_id = league_key.rsplit(marker, 1)[1]
        if not derived_league_id:
            raise LiveDraftValidationError("league_key must include a league identifier")
        if league_id is not None and str(league_id) != derived_league_id:
            raise LiveDraftValidationError("league_id must match league_key")
        league_id = derived_league_id

    live_state = load_live_draft(
        league_id=league_id,
        path=store_path,
        reject_ambiguous=True,
    )
    if live_state is None:
        return {
            "status": "error",
            "message": (
                "No synced live draft state was found. Load the Yahoo Draft Recorder "
                "extension, open Results → Round by Round, and rescan the page."
            ),
            "leagueId": league_id,
        }
    analyzed_snapshot = _snapshot_binding(live_state)
    profile = load_local_draft_profile(live_state["draft"], path=profile_path)
    if profile is None:
        try:
            profile = bind_default_local_draft_profile(
                live_state["draft"], profile_path=profile_path
            )
        except (
            LocalDraftProfileNotFoundError,
            LocalDraftProfileConflictError,
        ) as error:
            raise LiveDraftValidationError(str(error)) from error
        except LocalDraftProfileValidationError as error:
            raise LiveDraftValidationError(
                "The saved default draft profile is unavailable. Choose or clear it "
                "in the local dashboard."
            ) from error
    ranking_limit = max(25, min(int(ranking_count), 500))
    if profile is not None:
        raw_rankings = profile.get("rankings")
        rankings = (
            [dict(item) for item in raw_rankings[:ranking_limit] if isinstance(item, Mapping)]
            if isinstance(raw_rankings, list)
            else []
        )
        settings = profile.get("leagueSettings")
        league_info = dict(settings) if isinstance(settings, Mapping) else {}
        rankings_result: Mapping[str, Any] = {"rankings": rankings}
        league_result: Mapping[str, Any] = league_info
        rankings_source = _profile_source_label(profile)
        league_source = "user-imported league profile"
        season_value = profile.get("season")
        season = season_value if isinstance(season_value, int) else None
    else:
        # Keep all Yahoo calls serialized: concurrent 401 responses can race rotating token
        # refreshes. CPU-only scoring starts after releasing the Yahoo boundary.
        async with _YAHOO_RECOMMENDATION_LOCK:
            if league_key is None:
                leagues_result = await call_tool("ff_get_leagues")
                if not isinstance(leagues_result, Mapping):
                    raise LiveDraftValidationError(
                        "Yahoo league identity could not be resolved for the synced draft"
                    )
                league_key = _resolve_league_key(leagues_result, league_id)
            league_result = await call_tool("ff_get_league_info", league_key=league_key)
            league_mapping = league_result if isinstance(league_result, Mapping) else {}
            _validate_authenticated_team(
                league_mapping,
                live_state,
                league_key,
                required=require_authenticated_team,
            )
            rankings_result = await call_tool(
                "ff_get_draft_rankings",
                league_key=league_key,
                position="all",
                count=ranking_limit,
            )
        raw_rankings = (
            rankings_result.get("rankings", [])
            if isinstance(rankings_result, Mapping)
            else []
        )
        rankings = (
            [
                dict(item)
                for item in raw_rankings[:ranking_limit]
                if isinstance(item, Mapping)
            ]
            if isinstance(raw_rankings, list)
            else []
        )
        league_info = dict(league_result) if isinstance(league_result, Mapping) else {}
        rankings_source = "Yahoo pre-draft rankings"
        league_source = "Yahoo league info"
        season = None

    rankings, enrichment, enrichment_warnings = await _enrich_with_fantasypros(
        rankings,
        season=season,
        provider=fantasypros_provider or _FANTASYPROS_PROVIDER,
    )
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
    roster_slots_available = True
    if isinstance(league_result, Mapping) and (
        "error" in league_result or league_result.get("status") == "error"
    ):
        roster_slots_available = False
        league_warning = league_result.get("message") or league_result.get("error")
    else:
        roster_positions = league_info.get("roster_positions")
        if not isinstance(roster_positions, list):
            roster_positions = league_info.get("rosterPositions")
        if not isinstance(roster_positions, list) or not roster_positions:
            roster_slots_available = False
            source_name = "Local profile" if profile is not None else "Yahoo league"
            league_warning = (
                f"{source_name} roster positions are unavailable; using 1QB defaults"
            )
    if league_warning:
        warning_prefix = "Local profile" if profile is not None else "Yahoo league info"
        result["warnings"].append(f"{warning_prefix}: {league_warning}")
        if result.get("status") == "success":
            result["status"] = "degraded"
    result["warnings"].extend(enrichment_warnings)
    if enrichment.get("status") != "success" and result.get("status") == "success":
        result["status"] = "degraded"
    capabilities = result.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities["rosterSlotsAvailable"] = roster_slots_available
    critic_provider = (
        _DATABRICKS_ADVISORY_CRITIC
        if advisory_critic is None
        else advisory_critic
    )
    advisory = await _run_advisory_critic(result, critic_provider)
    current_state = load_live_draft(
        league_id=league_id,
        path=store_path,
        reject_ambiguous=True,
    )
    if (
        current_state is None
        or _snapshot_binding(current_state) != analyzed_snapshot
        or current_state != live_state
    ):
        return _refresh_required_result(league_id)
    current_profile = load_local_draft_profile(current_state["draft"], path=profile_path)
    if current_profile != profile:
        return _profile_refresh_required_result(league_id)
    if advisory is not None:
        result["advisoryCritic"] = advisory.to_dict()
        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            capabilities["llmOnRequestPath"] = True
    result["leagueKey"] = league_key
    result["leagueId"] = live_state.get("draft", {}).get("leagueId")
    result["dataSources"] = {
        "liveState": "local browser extension",
        "rankings": rankings_source,
        "league": league_source,
        "injuryNews": "FantasyPros public API",
    }
    result["enrichment"] = enrichment
    if profile is not None:
        result["profile"] = {
            "source": profile.get("source"),
            "season": profile.get("season"),
            "importedAt": profile.get("importedAt"),
            "provenance": profile.get("provenance"),
        }
    return result
