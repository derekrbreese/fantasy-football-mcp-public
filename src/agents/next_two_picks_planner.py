"""Pure, bounded planning for a user's next two snake-draft selections.

This module deliberately consumes already-scored candidates.  It performs no I/O and
does not alter the authoritative recommendation order.  Availability probabilities
are transparent ADP heuristics and are not calibrated.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import islice
from typing import Any

_MAX_INPUT_CANDIDATES = 20
_MAX_PLANNING_CANDIDATES = 12
_MAX_NOW_OPTIONS = 3
_MAX_REASONS = 3
_MAX_TEAM_COUNT = 20
_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _integer(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _candidate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    player = value.get("player")
    details = value.get("specialistDetails")
    roster = details.get("rosterConstruction") if isinstance(details, Mapping) else None
    if not isinstance(player, Mapping):
        return None
    name = str(player.get("name") or "").strip()[:120]
    position = str(player.get("position") or "").strip().upper()[:16]
    score = _finite_number(value.get("overallScore"))
    rank = _finite_number(player.get("rank"))
    if not name or position not in _POSITIONS or score is None:
        return None
    adp_is_available = (
        player.get("adpAvailable") is True or player.get("adpProvided") is True
    )
    adp = _finite_number(player.get("adp")) if adp_is_available else None
    return {
        "name": name,
        "position": position,
        "team": str(player.get("team") or "").strip()[:16],
        "score": round(max(0.0, min(100.0, score)), 2),
        "rank": rank if rank is not None else 10_000.0,
        "adp": adp,
        "roster": dict(roster) if isinstance(roster, Mapping) else {},
    }


def _brief(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": candidate["name"],
        "position": candidate["position"],
        "team": candidate["team"],
        "score": candidate["score"],
    }


def _snake_pick(round_number: int, draft_slot: int, team_count: int) -> int:
    in_round = draft_slot if round_number % 2 else team_count - draft_slot + 1
    return (round_number - 1) * team_count + in_round


def _next_two_user_picks(state: Mapping[str, Any]) -> list[int]:
    current = _integer(state.get("currentOverallPick"), 1, 100_000)
    team_count = _integer(state.get("teamCount"), 2, _MAX_TEAM_COUNT)
    slot = _integer(state.get("userDraftSlot"), 1, team_count or _MAX_TEAM_COUNT)
    if current is None or team_count is None or slot is None or slot > team_count:
        return []
    start_round = ((current - 1) // team_count) + 1
    picks: list[int] = []
    for round_number in range(start_round, start_round + 6):
        pick = _snake_pick(round_number, slot, team_count)
        if pick >= current:
            picks.append(pick)
        if len(picks) == 2:
            break
    expected_next = _integer(state.get("nextUserPick"), current, 100_000)
    if len(picks) != 2 or expected_next is None or picks[0] != expected_next:
        return []
    return picks


def _uncalibrated_survival(adp: float | None, following_pick: int) -> float | None:
    if adp is None:
        return None
    exponent = (following_pick - adp) / 6.0
    if exponent >= 60:
        probability = 0.0
    elif exponent <= -60:
        probability = 1.0
    else:
        probability = 1.0 / (1.0 + math.exp(exponent))
    return round(probability, 4)


def _roster_bonus(now: Mapping[str, Any], later: Mapping[str, Any]) -> tuple[float, str]:
    detail = later.get("roster")
    if not isinstance(detail, Mapping):
        return 0.0, "Roster-fit detail is unavailable for the next-turn option."
    current = _integer(detail.get("positionCount"), 0, 40)
    required = _integer(detail.get("required"), 0, 40)
    target = _integer(detail.get("target"), 0, 40)
    if current is None or required is None or target is None:
        return 0.0, "Roster-fit detail is unavailable for the next-turn option."
    after_now = current + int(now["position"] == later["position"])
    if after_now < required:
        return 10.0, f"{later['position']} would still fill an open starter or flex need."
    if after_now < target:
        return 4.0, f"{later['position']} would add useful position depth."
    return 0.0, f"{later['position']} would be beyond the current depth target."


def _best_next_turn(
    now: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], following_pick: int
) -> dict[str, Any] | None:
    choices: list[tuple[float, float, str, dict[str, Any]]] = []
    for later in candidates:
        if later["name"] == now["name"] and later["position"] == now["position"]:
            continue
        probability = _uncalibrated_survival(later.get("adp"), following_pick)
        availability_weight = 0.65 if probability is None else 0.5 + probability * 0.5
        diversity = 6.0 if now["position"] != later["position"] else 0.0
        roster_bonus, roster_reason = _roster_bonus(now, later)
        combined = (
            0.55 * now["score"]
            + 0.35 * later["score"] * availability_weight
            + diversity
            + roster_bonus
        )
        reasons = [roster_reason]
        if diversity:
            reasons.insert(0, "The pair spreads roster investment across positions.")
        if probability is None:
            reasons.append("Next-turn availability is unknown because actual ADP is missing.")
        else:
            reasons.append("Next-turn availability uses actual ADP only.")
        pair = {
            "now": _brief(now),
            "nextTurn": _brief(later),
            "positions": [now["position"], later["position"]],
            "combinedScore": round(max(0.0, min(100.0, combined)), 2),
            "nextTurnAvailabilityProbability": probability,
            "probabilityCalibrated": False,
            "reasons": reasons[:_MAX_REASONS],
        }
        choices.append((combined, later["rank"], later["name"], pair))
    if not choices:
        return None
    choices.sort(key=lambda item: (-item[0], item[1], item[2]))
    return choices[0][3]


def _now_options(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    selected = [dict(candidates[0])]
    remaining = list(candidates[1:])
    if remaining:
        selected.append(dict(remaining.pop(0)))
    while remaining and len(selected) < _MAX_NOW_OPTIONS:
        represented = {item["position"] for item in selected}
        different = next(
            (index for index, item in enumerate(remaining) if item["position"] not in represented),
            0,
        )
        selected.append(dict(remaining.pop(different)))
    return selected


def plan_next_two_picks(
    recommendations: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    *,
    unresolved_drafted: int = 0,
) -> dict[str, Any]:
    """Return a bounded, deterministic two-selection plan from scored candidates."""

    health = state.get("health") if isinstance(state, Mapping) else None
    complete = isinstance(health, Mapping) and health.get("complete") is True
    base = {
        "status": "blocked" if not complete else "unavailable",
        "method": (
            "bounded deterministic candidate-pair scoring using authoritative ledger order, "
            "roster needs, recommendation scores, and actual-ADP survival"
        ),
        "probabilitiesCalibrated": False,
        "primaryNow": None,
        "fallbacksNow": [],
        "nextUserPicks": [],
        "combinations": [],
        "uncertainties": [],
        "summary": "The authoritative ledger must be complete before a two-pick plan is shown.",
    }
    if not complete:
        return base

    parsed = [
        candidate
        for raw in islice(recommendations, _MAX_INPUT_CANDIDATES)
        if (candidate := _candidate(raw)) is not None
    ]
    parsed.sort(key=lambda item: (-item["score"], item["rank"], item["name"]))
    candidates = parsed[:_MAX_PLANNING_CANDIDATES]
    if not candidates:
        base["summary"] = "No trustworthy scored candidates are available for two-pick planning."
        return base

    now_options = _now_options(candidates)
    base["primaryNow"] = _brief(now_options[0])
    base["fallbacksNow"] = [_brief(item) for item in now_options[1:]]
    uncertainties: list[str] = []
    if health.get("fresh") is not True:
        uncertainties.append("Draft state is stale; refresh before acting on the plan.")
    if health.get("teamCountSource") != "league":
        uncertainties.append("League team count is not confirmed; snake-turn order may be inaccurate.")
    if max(0, int(unresolved_drafted)):
        uncertainties.append(
            "Drafted-player identities are unresolved; candidate availability may be incomplete."
        )

    next_picks = _next_two_user_picks(state)
    base["nextUserPicks"] = next_picks
    if not next_picks:
        uncertainties.append(
            "Your snake-draft order is unknown or inconsistent; next-turn combinations are omitted."
        )
    else:
        combinations = [
            pair
            for option in now_options
            if (pair := _best_next_turn(option, candidates, next_picks[1])) is not None
        ]
        base["combinations"] = combinations[:_MAX_NOW_OPTIONS]
        if not base["combinations"]:
            uncertainties.append(
                "Candidate depth is insufficient for a next-turn combination."
            )
        else:
            uncertainties.append(
                "Opponent-specific roster and tendency data is unavailable; "
                "next-turn availability uses ADP instead."
            )
        if any(
            pair["nextTurnAvailabilityProbability"] is None
            for pair in base["combinations"]
        ):
            uncertainties.append(
                "At least one next-turn option lacks actual ADP; its availability is unknown."
            )

    base["uncertainties"] = uncertainties[:6]
    base["status"] = "degraded" if uncertainties else "ready"
    base["summary"] = (
        "Use the primary now and re-run after every recorded pick; future availability is an "
        "uncalibrated heuristic."
        if not uncertainties
        else "The immediate board is usable, but uncertainty limits the two-pick projection."
    )
    return base
