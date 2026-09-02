"""Pure, bounded market signals for live-draft recommendation payloads.

This module deliberately consumes already-scored candidate mappings. It performs no
I/O and never substitutes ranking order for missing market ADP.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

_MAX_TEAM_COUNT = 20
_MAX_SLEEPER_WATCH = 5
_SLEEPER_START_ROUND = 7
_MARKET_METHOD = (
    "Uncalibrated deterministic rank-versus-ADP market heuristic; "
    "it does not predict player performance."
)
_ACTION_METHOD = (
    "Uncalibrated ADP return heuristic: take now below a 50% estimated return chance; "
    "can wait at or above 50%."
)
_POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST"}


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _optional_positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _position(value: Any) -> str:
    result = str(value or "").strip().upper()
    return _POSITION_ALIASES.get(result, result)


def _bounded_team_count(state: Mapping[str, Any]) -> int:
    return max(2, min(int(_number(state.get("teamCount"), 12)), _MAX_TEAM_COUNT))


def _market_source_metadata(
    value: Mapping[str, Any] | None, target_season: int
) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    raw_name = raw.get("name")
    name = (
        re.sub(r"[\x00-\x1f\x7f]", "", raw_name).strip()[:120]
        if isinstance(raw_name, str)
        else ""
    )
    raw_season = raw.get("season")
    season = (
        raw_season
        if isinstance(raw_season, int) and not isinstance(raw_season, bool)
        else (
            int(raw_season)
            if isinstance(raw_season, str) and raw_season.isdigit()
            else None
        )
    )
    raw_as_of = raw.get("asOf")
    parsed_as_of = _parse_time(raw_as_of.strip()) if isinstance(raw_as_of, str) else None
    as_of = (
        raw_as_of.strip()[:40]
        if parsed_as_of is not None and parsed_as_of.year == season
        else None
    )
    raw_basis = raw.get("asOfBasis", "source")
    as_of_basis = (
        raw_basis if raw_basis in {"source", "imported", "retrieved"} else "source"
    )
    return {
        "name": name or "Ranking source unavailable",
        "season": season,
        "targetSeason": target_season,
        "sameSeason": season == target_season,
        "asOf": as_of,
        "asOfBasis": as_of_basis,
    }


def _source_ready(source: Mapping[str, Any]) -> bool:
    return bool(
        source.get("sameSeason") is True
        and source.get("asOf")
        and source.get("asOfBasis") in {"source", "retrieved"}
        and source.get("name") != "Ranking source unavailable"
    )


def _risk_caution(risk: Mapping[str, Any]) -> dict[str, str] | None:
    if risk.get("fresh") is not True:
        return None
    status = str(risk.get("status") or "unknown").strip().lower()
    source = str(risk.get("source") or "sourced feed").strip()[:80]
    updated_at = str(risk.get("updatedAt") or "").strip()[:40]
    if status not in {"unknown", "healthy"}:
        message = f"Fresh {status} status from {source}"
    elif risk.get("basis") == "recent-news-category" and risk.get("newsFresh") is True:
        message = f"Fresh structured injury/transaction news from {source}"
    else:
        return None
    if updated_at:
        message = f"{message} · updated {updated_at}"
    return {"message": f"{message}."[:240], "source": source, "updatedAt": updated_at}


def _market_definitions(team_count: int) -> list[dict[str, str]]:
    return [
        {
            "code": "value",
            "label": "Value",
            "description": (
                f"Current pick is at least {team_count} picks (one league round) "
                "after a real ADP."
            ),
        },
        {
            "code": "sleeper-watch",
            "label": "Sleeper Watch",
            "description": (
                f"Real ADP is Round {_SLEEPER_START_ROUND} or later and source rank "
                f"beats ADP by at least {team_count} picks (one league round)."
            ),
        },
        {
            "code": "fade",
            "label": "Fade",
            "description": (
                f"Source rank trails real ADP by at least {team_count} picks "
                "(one league round); this is a market caution, not a do-not-draft command."
            ),
        },
    ]


def _decision_signals(
    item: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    market_trusted: bool,
) -> dict[str, Any]:
    player = item.get("player") if isinstance(item.get("player"), Mapping) else {}
    risk = item.get("risk") if isinstance(item.get("risk"), Mapping) else {}
    risk_caution = _risk_caution(risk)
    real_adp = (
        _optional_positive_number(player.get("adp"))
        if player.get("adpAvailable") is True
        else None
    )
    rank = _optional_positive_number(player.get("rank"))
    team_count = _bounded_team_count(state)
    current_pick = int(_number(state.get("currentOverallPick"), 0))
    next_pick = state.get("nextUserPick")
    badges: list[dict[str, str]] = []
    if market_trusted and real_adp is not None and rank is not None:
        if current_pick > 0 and current_pick - real_adp >= team_count:
            badges.append(
                {
                    "code": "value",
                    "label": "Value",
                    "detail": f"{current_pick - real_adp:g} picks past real ADP",
                }
            )
        if (
            real_adp >= team_count * (_SLEEPER_START_ROUND - 1) + 1
            and real_adp - rank >= team_count
        ):
            badges.append(
                {
                    "code": "sleeper-watch",
                    "label": "Sleeper Watch",
                    "detail": f"Ranked {real_adp - rank:g} picks ahead of real ADP",
                }
            )
        if rank - real_adp >= team_count:
            badges.append(
                {
                    "code": "fade",
                    "label": "Fade",
                    "detail": f"Ranked {rank - real_adp:g} picks behind real ADP",
                }
            )

    probability = _number(item.get("returnProbability"), math.nan)
    next_pick_available = (
        isinstance(next_pick, int) and not isinstance(next_pick, bool) and next_pick > 0
    )
    if (
        not market_trusted
        or real_adp is None
        or not next_pick_available
        or not math.isfinite(probability)
    ):
        action = {
            "code": "timing-unknown",
            "label": "Timing unknown",
            "reason": (
                "Take-now timing needs a complete trusted market source, real ADP, "
                "resolved drafted identities, and a known next pick."
            ),
            "method": _ACTION_METHOD,
            "calibrated": False,
        }
    else:
        percent = round(max(0.0, min(1.0, probability)) * 100)
        take_now = probability < 0.5
        action = {
            "code": "take-now" if take_now else "can-wait",
            "label": "Take now" if take_now else "Can wait",
            "reason": (
                f"Uncalibrated ADP heuristic estimates a {percent}% chance of reaching "
                f"your next pick ({next_pick})."
            ),
            "method": _ACTION_METHOD,
            "calibrated": False,
        }
    return {"badges": badges[:3], "action": action, "riskCaution": risk_caution}


def _market_signals(
    state: Mapping[str, Any],
    evaluated: Sequence[Mapping[str, Any]],
    *,
    source: Mapping[str, Any],
    ranking_rows: int,
    drafted_count: int,
    unresolved_drafted: int,
    counts_trustworthy: bool,
) -> dict[str, Any]:
    team_count = _bounded_team_count(state)
    health = state.get("health") if isinstance(state.get("health"), Mapping) else {}
    ledger_complete = health.get("complete") is True
    identities_resolved = counts_trustworthy and unresolved_drafted == 0
    source_ready = _source_ready(source)
    real_adp_count = sum(
        item.get("player", {}).get("adpAvailable") is True for item in evaluated
    )
    market_trusted = ledger_complete and identities_resolved and source_ready

    sleepers: list[Mapping[str, Any]] = []
    if market_trusted:
        sleepers = [
            item
            for item in evaluated
            if any(
                badge.get("code") == "sleeper-watch"
                for badge in item.get("decisionSignals", {}).get("badges", [])
                if isinstance(badge, Mapping)
            )
        ]
        sleepers.sort(
            key=lambda item: (
                -(
                    _number(item.get("player", {}).get("adp"), 0.0)
                    - _number(item.get("player", {}).get("rank"), 0.0)
                ),
                _number(item.get("player", {}).get("adp"), 10_000.0),
                _number(item.get("player", {}).get("rank"), 10_000.0),
                str(item.get("player", {}).get("name") or "").casefold(),
            )
        )

    visible_sleepers = []
    for item in sleepers[:_MAX_SLEEPER_WATCH]:
        player = item.get("player", {})
        rank = _number(player.get("rank"), 0.0)
        adp = _number(player.get("adp"), 0.0)
        discount = adp - rank
        signals = item.get("decisionSignals", {})
        visible_sleepers.append(
            {
                "player": {
                    "name": str(player.get("name") or "Unknown player")[:120],
                    "position": _position(player.get("position"))[:16],
                    "team": str(player.get("team") or "")[:16],
                },
                "rank": round(rank, 2),
                "adp": round(adp, 2),
                "discountPicks": round(discount, 2),
                "discountRounds": round(discount / team_count, 2),
                "marketRound": max(1, math.ceil(adp / team_count)),
                "summary": (
                    f"Rank {rank:g} is {discount:g} picks "
                    f"({discount / team_count:.2f} league rounds) ahead of real ADP {adp:g}."
                ),
                "badges": list(signals.get("badges", []))[:3],
                "action": dict(signals.get("action", {})),
                "riskCaution": (
                    dict(signals["riskCaution"])
                    if isinstance(signals.get("riskCaution"), Mapping)
                    else None
                ),
            }
        )

    no_adp_count = sum(
        item.get("player", {}).get("adpAvailable") is not True for item in evaluated
    )
    hidden_sleepers = max(0, len(sleepers) - len(visible_sleepers))
    if not ledger_complete or not identities_resolved:
        status = "blocked"
        message = (
            "Sleeper Watch is blocked until the authoritative ledger is complete and "
            "every drafted identity resolves against the bounded ranking pool."
        )
    elif not source_ready or real_adp_count == 0:
        status = "unavailable"
        message = (
            "Sleeper Watch needs dated same-season ranking metadata and explicitly "
            "supplied real ADP values."
        )
    else:
        status = "available"
        message = (
            f"{len(visible_sleepers)} of {len(sleepers)} qualifying late-market "
            "candidate(s) shown from the bounded ranking frontier."
        )

    trust = [
        {
            "code": "ledger-complete",
            "passed": ledger_complete,
            "message": (
                "Authoritative numbered ledger is complete."
                if ledger_complete
                else "Authoritative numbered ledger is incomplete or ambiguous."
            ),
        },
        {
            "code": "drafted-identities-resolved",
            "passed": identities_resolved,
            "message": (
                "Every drafted row resolved conservatively against this ranking frontier."
                if identities_resolved
                else (
                    f"{unresolved_drafted} drafted player identity value(s) remain unresolved."
                    if counts_trustworthy
                    else "Drafted identity resolution is not evaluated from a defective ledger."
                )
            ),
        },
        {
            "code": "same-season-source",
            "passed": source_ready,
            "message": (
                "Ranking source supplies a dated same-season market snapshot."
                if source_ready
                else (
                    "Ranking source name, source/retrieval date, or same-season "
                    "metadata is unavailable."
                )
            ),
        },
    ]
    exclusions = [
        {
            "code": "drafted",
            "count": drafted_count if counts_trustworthy else None,
            "known": counts_trustworthy,
            "message": (
                f"{drafted_count} ranking candidate(s) confidently matched drafted players."
                if counts_trustworthy
                else "Drafted-candidate exclusions are not counted from a defective ledger."
            ),
        },
        {
            "code": "unresolved-drafted-identity",
            "count": unresolved_drafted if counts_trustworthy else None,
            "known": counts_trustworthy,
            "message": (
                f"{unresolved_drafted} drafted identity value(s) were unresolved."
                if counts_trustworthy
                else "Unresolved identities are not counted from a defective ledger."
            ),
        },
        {
            "code": "no-real-adp",
            "count": no_adp_count,
            "known": True,
            "message": f"{no_adp_count} available ranking candidate(s) have no real ADP.",
        },
        {
            "code": "outside-displayed-frontier",
            "count": hidden_sleepers,
            "known": market_trusted,
            "message": (
                f"{hidden_sleepers} qualifying candidate(s) are outside the "
                f"{_MAX_SLEEPER_WATCH}-player displayed frontier."
                if market_trusted
                else "Hidden qualifying candidates are counted only after trust checks pass."
            ),
        },
    ]
    return {
        "status": status,
        "calibrated": False,
        "method": _MARKET_METHOD,
        "actionMethod": _ACTION_METHOD,
        "message": message,
        "scope": (
            f"Counts cover only {ranking_rows} supplied ranking row(s); players outside "
            "that bounded input frontier are unknown."
        ),
        "source": dict(source),
        "definitions": _market_definitions(team_count),
        "trust": trust,
        "exclusions": exclusions,
        "sleeperWatch": visible_sleepers if status == "available" else [],
    }


def build_market_decision_payload(
    state: Mapping[str, Any],
    evaluated: Sequence[Mapping[str, Any]],
    *,
    source: Mapping[str, Any] | None,
    target_season: int,
    ranking_rows: int,
    drafted_count: int,
    unresolved_drafted: int,
    counts_trustworthy: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return copied candidate payloads plus their bounded market summary."""

    source_metadata = _market_source_metadata(source, target_season)
    health = state.get("health") if isinstance(state.get("health"), Mapping) else {}
    market_trusted = bool(
        health.get("complete") is True
        and unresolved_drafted == 0
        and _source_ready(source_metadata)
    )
    enriched = []
    for original in evaluated:
        item = dict(original)
        item["decisionSignals"] = _decision_signals(
            item,
            state,
            market_trusted=market_trusted,
        )
        enriched.append(item)
    market = _market_signals(
        state,
        enriched,
        source=source_metadata,
        ranking_rows=max(0, int(_number(ranking_rows, 0))),
        drafted_count=max(0, int(_number(drafted_count, 0))),
        unresolved_drafted=max(0, int(_number(unresolved_drafted, 0))),
        counts_trustworthy=counts_trustworthy,
    )
    return enriched, market
