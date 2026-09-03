"""Application service that joins private live state with Yahoo draft data."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
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
from src.services.sleeper_player_provider import SleeperPlayerProvider
from src.services.yahoo_player_identity import normalize_yahoo_player_key

ToolCaller = Callable[..., Awaitable[dict[str, Any]]]
_YAHOO_RECOMMENDATION_LOCK = asyncio.Lock()
_DRAFT_IDENTITY_FIELDS = ("sport", "leagueId", "teamId", "sessionKey")
_FANTASYPROS_ENRICHMENT_TIMEOUT_SECONDS = 10.0
_SLEEPER_ENRICHMENT_TIMEOUT_SECONDS = 6.0
_FANTASYPROS_PROVIDER = FantasyProsProvider()
_SLEEPER_PLAYER_PROVIDER = SleeperPlayerProvider()
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
    "projected_points",
    "projected_opportunities",
    "projection_opportunity_kind",
    "projection_source",
    "projection_season",
    "projection_scoring",
    "projection_source_as_of",
    "projection_fetched_at",
    "projection_stale",
    "average_draft_position",
    "adp_source",
    "adp_season",
    "adp_scoring",
    "adp_source_as_of",
    "adp_fetched_at",
    "adp_stale",
)
_POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST"}
_BREAKOUT_OPPORTUNITY_KINDS = {
    "RB": {"touches"},
    "WR": {"targets", "receptions"},
    "TE": {"targets", "receptions"},
}


def _sanitize_ranking_player_keys(
    rankings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only one canonical Yahoo key and discard untrusted aliases."""

    sanitized = []
    for ranking in rankings:
        candidate = dict(ranking)
        supplied = [
            candidate.pop(field) for field in ("player_key", "playerKey") if field in candidate
        ]
        valid = {
            player_key
            for value in supplied
            if (player_key := normalize_yahoo_player_key(value)) is not None
        }
        if len(valid) == 1:
            candidate["player_key"] = valid.pop()
        sanitized.append(candidate)
    return sanitized


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
        return DatabricksAdvisoryResult.unavailable("timeout", model=_advisory_model(critic))
    except Exception:
        return DatabricksAdvisoryResult.unavailable("provider_error", model=_advisory_model(critic))
    if not isinstance(advisory, DatabricksAdvisoryResult):
        return DatabricksAdvisoryResult.unavailable("provider_error", model=_advisory_model(critic))
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


def _same_enrichment_identity(ranking: Mapping[str, Any], update: Mapping[str, Any]) -> bool:
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


def _resolve_projection_scoring(
    league_info: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    for field, source in (
        ("scoringFormat", "leagueSettings.scoringFormat"),
        ("scoring_format", "league.scoring_format"),
    ):
        value = league_info.get(field)
        if value in {"STD", "HALF", "PPR"}:
            return str(value), {"value": value, "source": source, "defaulted": False}
    for field, source in (
        ("pointsPerReception", "league.pointsPerReception"),
        ("points_per_reception", "league.points_per_reception"),
    ):
        value = league_info.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        scoring = {0.0: "STD", 0.5: "HALF", 1.0: "PPR"}.get(float(value))
        if scoring is not None:
            return scoring, {"value": scoring, "source": source, "defaulted": False}
    return "HALF", {"value": "HALF", "source": "default", "defaulted": True}


def _resolve_projection_season(
    value: Any,
    *,
    source: str,
    current_year: int | None = None,
) -> tuple[int, dict[str, Any]]:
    utc_year = current_year if current_year is not None else datetime.now(timezone.utc).year
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}", value):
        parsed = int(value)
    else:
        parsed = None
    if parsed is not None and 2012 <= parsed <= utc_year + 1:
        return parsed, {"value": parsed, "source": source, "defaulted": False}
    return utc_year, {
        "value": utc_year,
        "source": "currentUtcYear",
        "defaulted": True,
        "reason": f"{source}_unavailable_or_invalid",
    }


def _safe_projection_evidence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for field in (
        "status",
        "source",
        "season",
        "week",
        "scoring",
        "sourceAsOf",
        "fetchedAt",
        "stale",
        "refreshFailed",
        "availablePlayers",
        "experienceYearsAvailable",
        "returnedCount",
        "reportedCount",
        "reportedLimit",
        "publicApiLimited",
    ):
        if field in value and isinstance(value[field], (str, int, bool, type(None))):
            result[field] = value[field]
    positions = value.get("positions")
    if (
        isinstance(positions, list)
        and len(positions) <= 3
        and all(position in {"RB", "WR", "TE"} for position in positions)
    ):
        result["positions"] = list(positions)
    return result


def _safe_adp_evidence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for field in (
        "status",
        "reason",
        "source",
        "season",
        "scoring",
        "sourceAsOf",
        "fetchedAt",
        "stale",
        "refreshFailed",
        "availablePlayers",
        "publicApiLimited",
    ):
        if field in value and isinstance(value[field], (str, int, bool, type(None))):
            result[field] = value[field]
    return result


def _ranking_declares_adp(value: Mapping[str, Any]) -> bool:
    """Treat any supported input ADP field as provenance that must not be mixed."""

    return "average_draft_position" in value or "adp" in value


def _valid_provider_time(value: Any, season: int) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 40
        or value != value.strip()
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.year == season


def _positive_finite_number(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    parsed = float(value)
    return parsed if 0 < parsed < float("inf") else None


def _fantasypros_market_source(
    original_rankings: list[dict[str, Any]],
    enriched_rankings: list[dict[str, Any]],
    enrichment: Mapping[str, Any],
    *,
    season: int,
    scoring: str,
) -> dict[str, Any] | None:
    """Return provider market provenance only for one unambiguous ADP source."""

    if scoring not in {"STD", "HALF", "PPR"}:
        return None
    if any(_ranking_declares_adp(item) for item in original_rankings):
        return None
    evidence = enrichment.get("adpEvidence")
    if not isinstance(evidence, Mapping):
        return None
    fetched_at = evidence.get("fetchedAt")
    source_as_of = evidence.get("sourceAsOf")
    available_players = evidence.get("availablePlayers")
    adp_players = enrichment.get("adpPlayers")
    if (
        evidence.get("status") != "available"
        or evidence.get("source") != "FantasyPros"
        or type(evidence.get("season")) is not int
        or evidence.get("season") != season
        or evidence.get("scoring") != scoring
        or evidence.get("stale") is not False
        or not _valid_provider_time(fetched_at, season)
        or type(available_players) is not int
        or available_players < 1
        or type(adp_players) is not int
        or adp_players < 1
        or available_players < adp_players
        or (source_as_of is not None and not _valid_provider_time(source_as_of, season))
    ):
        return None

    provider_rows = [item for item in enriched_rankings if "average_draft_position" in item]
    if len(provider_rows) != adp_players:
        return None
    for item in provider_rows:
        if (
            item.get("identityResolved") is not True
            or _positive_finite_number(item.get("average_draft_position")) is None
            or item.get("adp_source") != "FantasyPros"
            or type(item.get("adp_season")) is not int
            or item.get("adp_season") != season
            or item.get("adp_scoring") != scoring
            or item.get("adp_source_as_of") != source_as_of
            or item.get("adp_fetched_at") != fetched_at
            or item.get("adp_stale") is not False
        ):
            return None
    return {
        "name": "FantasyPros",
        "season": season,
        "asOf": fetched_at,
        "asOfBasis": "retrieved",
    }


def _without_fantasypros_adp(
    rankings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove provider ADP whose provenance could not be safely established."""

    sanitized: list[dict[str, Any]] = []
    for ranking in rankings:
        candidate = {
            key: value
            for key, value in ranking.items()
            if key != "average_draft_position" and not key.startswith("adp_")
        }
        sanitized.append(candidate)
    return sanitized


def _merge_fantasypros_updates(
    rankings: list[dict[str, Any]], provider_result: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_updates = provider_result.get("players")
    updates = raw_updates if isinstance(raw_updates, list) else []
    merged: list[dict[str, Any]] = []
    resolved = 0
    fresh_injuries = 0
    fresh_news = 0
    projected = 0
    adp = 0
    imported_adp = sum(_ranking_declares_adp(ranking) for ranking in rankings)
    for index, ranking in enumerate(rankings):
        candidate = dict(ranking)
        update = updates[index] if index < len(updates) else None
        if isinstance(update, Mapping) and _same_enrichment_identity(candidate, update):
            accept_provider_adp = imported_adp == 0 and update.get("identityResolved") is True
            for field in _FANTASYPROS_FIELDS:
                if not accept_provider_adp and (
                    field == "average_draft_position" or field.startswith("adp_")
                ):
                    continue
                if field in update:
                    candidate[field] = update[field]
            if update.get("identityResolved") is True:
                resolved += 1
            if update.get("injury_fresh") is True:
                fresh_injuries += 1
            if update.get("news_fresh") is True:
                fresh_news += 1
            if "projected_points" in update and "projected_opportunities" in update:
                projected += 1
            if accept_provider_adp and "average_draft_position" in update:
                adp += 1
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
        "projectedPlayers": projected,
        "adpPlayers": adp,
        "importedAdpPlayers": imported_adp,
    }


async def _enrich_with_fantasypros(
    rankings: list[dict[str, Any]],
    *,
    season: int | None,
    league_info: Mapping[str, Any],
    provider: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    projection_scoring, scoring_provenance = _resolve_projection_scoring(league_info)
    try:
        raw_result = await asyncio.wait_for(
            provider.get_player_updates(
                rankings,
                year=season,
                week=0,
                projection_scoring=projection_scoring,
            ),
            timeout=_FANTASYPROS_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        raw_result = {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": ["FantasyPros evidence timed out; missing data remains unknown"],
        }
    except Exception:
        raw_result = {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": ["FantasyPros evidence is temporarily unavailable"],
        }
    if not isinstance(raw_result, Mapping):
        raw_result = {
            "status": "unavailable",
            "provider": "FantasyPros",
            "players": [],
            "warnings": ["FantasyPros evidence is temporarily unavailable"],
        }
    merged, summary = _merge_fantasypros_updates(rankings, raw_result)
    summary["projectionScoring"] = scoring_provenance
    projection_evidence = _safe_projection_evidence(raw_result.get("projectionEvidence"))
    if projection_evidence is not None:
        summary["projectionEvidence"] = projection_evidence
    adp_evidence = _safe_adp_evidence(raw_result.get("adpEvidence"))
    if adp_evidence is not None:
        summary["adpEvidence"] = adp_evidence
    warnings: list[str] = []
    raw_warnings = raw_result.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings[:6]:
            if isinstance(warning, str) and warning.startswith("FantasyPros"):
                warnings.append(warning[:240])
    if summary["status"] == "unavailable" and not warnings:
        warnings.append("FantasyPros evidence is unavailable; missing data remains unknown")
    return merged, summary, warnings


def _projection_as_of(candidate: Mapping[str, Any], season: int) -> str | None:
    for field in ("projection_source_as_of", "projection_fetched_at"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value or len(value) > 40:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed.year == season:
            return parsed.date().isoformat()
    return None


def _merge_sleeper_breakout_evidence(
    rankings: list[dict[str, Any]],
    provider_result: Mapping[str, Any],
    *,
    season: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Complete FantasyPros projection evidence with matched Sleeper experience."""

    raw_updates = provider_result.get("players")
    updates = raw_updates if isinstance(raw_updates, list) else []
    merged: list[dict[str, Any]] = []
    experience_players = 0
    generated = 0
    for index, ranking in enumerate(rankings):
        candidate = dict(ranking)
        update = updates[index] if index < len(updates) else None
        if not isinstance(update, Mapping) or not _same_enrichment_identity(candidate, update):
            merged.append(candidate)
            continue
        experience = update.get("experience_years")
        experience_valid = (
            update.get("identityResolved") is True
            and update.get("experience_source") == "Sleeper"
            and type(experience) is int
            and 0 <= experience <= 30
        )
        if experience_valid:
            experience_players += 1
            candidate["experience_years"] = experience
            candidate["experience_source"] = "Sleeper"
        position = _player_identity(candidate)[1]
        opportunity_kind = candidate.get("projection_opportunity_kind")
        points = _positive_finite_number(candidate.get("projected_points"))
        opportunities = _positive_finite_number(candidate.get("projected_opportunities"))
        as_of = _projection_as_of(candidate, season)
        if (
            "breakout_evidence" not in candidate
            and experience_valid
            and candidate.get("identityResolved") is True
            and candidate.get("projection_source") == "FantasyPros"
            and candidate.get("projection_season") == season
            and candidate.get("projection_stale") is False
            and opportunity_kind in _BREAKOUT_OPPORTUNITY_KINDS.get(position, ())
            and points is not None
            and opportunities is not None
            and as_of is not None
        ):
            candidate["breakout_evidence"] = {
                "source": "FantasyPros + Sleeper",
                "as_of": as_of,
                "projected_points": points,
                "projected_opportunities": opportunities,
                "opportunity_kind": opportunity_kind,
                "experience_years": experience,
            }
            generated += 1
        merged.append(candidate)
    status = provider_result.get("status")
    return merged, {
        "provider": "Sleeper",
        "status": status if status in {"success", "degraded", "unavailable"} else "unavailable",
        "catalogFetchedAt": (
            provider_result.get("catalogFetchedAt")
            if isinstance(provider_result.get("catalogFetchedAt"), str)
            else None
        ),
        "cacheStale": provider_result.get("cacheStale") is True,
        "refreshFailed": provider_result.get("refreshFailed") is True,
        "catalogPlayers": (
            provider_result.get("catalogPlayers")
            if type(provider_result.get("catalogPlayers")) is int
            else 0
        ),
        "identityResolvedPlayers": experience_players,
        "generatedBreakoutEvidencePlayers": generated,
    }


async def _enrich_breakout_experience(
    rankings: list[dict[str, Any]],
    *,
    season: int,
    provider: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    if not any(
        ranking.get("projection_source") == "FantasyPros"
        and "projected_points" in ranking
        and "projected_opportunities" in ranking
        for ranking in rankings
    ):
        return (
            rankings,
            {
                "provider": "Sleeper",
                "status": "not_needed",
                "identityResolvedPlayers": 0,
                "generatedBreakoutEvidencePlayers": 0,
            },
            [],
        )
    try:
        raw_result = await asyncio.wait_for(
            provider.get_player_experience(rankings),
            timeout=_SLEEPER_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        raw_result = {
            "status": "unavailable",
            "players": [],
            "warnings": ["Sleeper player experience timed out; Breakout Watch may be unavailable"],
        }
    except Exception:
        raw_result = {
            "status": "unavailable",
            "players": [],
            "warnings": [
                "Sleeper player experience is temporarily unavailable; Breakout Watch may be unavailable"
            ],
        }
    if not isinstance(raw_result, Mapping):
        raw_result = {
            "status": "unavailable",
            "players": [],
            "warnings": [
                "Sleeper player experience is temporarily unavailable; Breakout Watch may be unavailable"
            ],
        }
    merged, summary = _merge_sleeper_breakout_evidence(rankings, raw_result, season=season)
    warnings = []
    raw_warnings = raw_result.get("warnings")
    if isinstance(raw_warnings, list):
        warnings.extend(
            warning[:240]
            for warning in raw_warnings[:2]
            if isinstance(warning, str) and warning.startswith("Sleeper")
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


def _profile_market_source(profile: Mapping[str, Any], name: str) -> dict[str, Any]:
    provenance = profile.get("provenance")
    source_as_of = provenance.get("asOf") if isinstance(provenance, Mapping) else None
    imported_at = profile.get("importedAt")
    if isinstance(source_as_of, str) and source_as_of:
        as_of = source_as_of
        basis = "source"
    else:
        as_of = imported_at if isinstance(imported_at, str) else None
        basis = "imported"
    return {
        "name": name,
        "season": profile.get("season"),
        "asOf": as_of,
        "asOfBasis": basis,
    }


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
        raise LiveDraftValidationError("Yahoo league identity is ambiguous for the synced draft")
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
    sleeper_player_provider: Any | None = None,
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
            raise LiveDraftValidationError("league_key must contain a Yahoo .l. league identifier")
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
        season, season_provenance = _resolve_projection_season(
            profile.get("season"), source="localProfile.season"
        )
        market_source = _profile_market_source(profile, rankings_source)
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
            rankings_result.get("rankings", []) if isinstance(rankings_result, Mapping) else []
        )
        rankings = (
            [dict(item) for item in raw_rankings[:ranking_limit] if isinstance(item, Mapping)]
            if isinstance(raw_rankings, list)
            else []
        )
        league_info = dict(league_result) if isinstance(league_result, Mapping) else {}
        rankings_source = "Yahoo pre-draft rankings"
        league_source = "Yahoo league info"
        season, season_provenance = _resolve_projection_season(
            league_info.get("season"), source="yahooLeagueInfo.season"
        )
        market_source = {
            "name": rankings_source,
            "season": season,
            "asOf": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "asOfBasis": "retrieved",
        }

    rankings = _sanitize_ranking_player_keys(rankings)
    rankings_before_enrichment = [dict(item) for item in rankings]
    rankings, enrichment, enrichment_warnings = await _enrich_with_fantasypros(
        rankings,
        season=season,
        league_info=league_info,
        provider=fantasypros_provider or _FANTASYPROS_PROVIDER,
    )
    rankings, sleeper_enrichment, sleeper_warnings = await _enrich_breakout_experience(
        rankings,
        season=season,
        provider=sleeper_player_provider or _SLEEPER_PLAYER_PROVIDER,
    )
    enrichment["sleeperExperience"] = sleeper_enrichment
    enrichment_warnings.extend(sleeper_warnings)
    projection_evidence = enrichment.get("projectionEvidence")
    if isinstance(projection_evidence, dict):
        projection_evidence["experienceYearsAvailable"] = (
            sleeper_enrichment.get("identityResolvedPlayers", 0) > 0
        )
    enrichment["seasonProvenance"] = season_provenance
    if season_provenance["defaulted"] is True:
        enrichment_warnings.append(
            "FantasyPros season defaulted to the current UTC year because the league "
            "season was unavailable or invalid"
        )
    scoring = enrichment.get("projectionScoring", {}).get("value")
    provider_market_source = _fantasypros_market_source(
        rankings_before_enrichment,
        rankings,
        enrichment,
        season=season,
        scoring=scoring if isinstance(scoring, str) else "",
    )
    if provider_market_source is not None:
        market_source = provider_market_source
    elif enrichment.get("adpPlayers", 0) > 0 and not any(
        _ranking_declares_adp(item) for item in rankings_before_enrichment
    ):
        # Provider ADP without fully valid provenance must neither influence scores
        # nor inherit the label of an unrelated ranking source.
        rankings = _without_fantasypros_adp(rankings)
        market_source = None
    engine = LiveDraftRecommendationEngine(simulations=max(0, min(int(simulations), 512)))
    result = await asyncio.to_thread(
        engine.recommend,
        live_state,
        rankings if isinstance(rankings, list) else [],
        league_info,
        strategy=strategy,
        count=max(1, min(int(count), 20)),
        market_source=market_source,
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
            league_warning = f"{source_name} roster positions are unavailable; using 1QB defaults"
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
    critic_provider = _DATABRICKS_ADVISORY_CRITIC if advisory_critic is None else advisory_critic
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
    if sleeper_enrichment.get("status") != "not_needed":
        result["dataSources"]["playerExperience"] = "Sleeper player catalog"
    result["enrichment"] = enrichment
    if profile is not None:
        result["profile"] = {
            "source": profile.get("source"),
            "season": profile.get("season"),
            "importedAt": profile.get("importedAt"),
            "provenance": profile.get("provenance"),
        }
    return result
