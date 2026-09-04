"""Low-latency specialist suite for recommendations from a synced live draft.

The classes in this module are deliberately deterministic and credential-free.  They
are called "agents" because each owns one decision domain, but they are pure in-process
scorers rather than independent LLM calls.  Optional enrichment can be supplied in the
ranking payload without making network calls on the draft-clock path.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from src.agents.breakout_watch import evaluate_breakout_watch
from src.agents.draft_market_signals import build_market_decision_payload
from src.agents.next_two_picks_planner import plan_next_two_picks
from src.services.yahoo_player_identity import normalize_yahoo_player_key

_POSITION_ALIASES = {
    "DEF": "DST",
    "D/ST": "DST",
    "W/R/T": "FLEX",
    "Q/W/R/T": "SUPERFLEX",
    "OP": "SUPERFLEX",
}
# Yahoo uses JAX while several ranking providers use JAC. Keep this map small and
# explicit so initialed-name matching still requires a known-equivalent team.
_NFL_TEAM_ALIASES = {"JAC": "JAX"}
_DEFAULT_ROSTER = [
    {"position": "QB", "count": 1},
    {"position": "RB", "count": 2},
    {"position": "WR", "count": 2},
    {"position": "TE", "count": 1},
    {"position": "FLEX", "count": 1},
    {"position": "K", "count": 1},
    {"position": "DST", "count": 1},
    {"position": "BN", "count": 6, "position_type": "BN"},
]
_STRATEGY_WEIGHTS = {
    "balanced": {
        "value": 0.32,
        "rosterConstruction": 0.24,
        "draftPlan": 0.18,
        "draftDynamics": 0.10,
        "opponentModel": 0.12,
        "riskNews": 0.12,
        "scenario": 0.10,
    },
    "aggressive": {
        "value": 0.28,
        "rosterConstruction": 0.15,
        "draftPlan": 0.15,
        "draftDynamics": 0.12,
        "opponentModel": 0.12,
        "riskNews": 0.08,
        "scenario": 0.25,
    },
    "conservative": {
        "value": 0.30,
        "rosterConstruction": 0.25,
        "draftPlan": 0.18,
        "draftDynamics": 0.08,
        "opponentModel": 0.10,
        "riskNews": 0.22,
        "scenario": 0.05,
    },
}
_DRAFT_PLANS = frozenset(
    {"balanced_rb_wr", "hero_rb", "wr_heavy", "rb_heavy", "best_available"}
)
_COCKPIT_POSITIONS = ("OVERALL", "QB", "RB", "WR", "TE", "FLEX", "K", "DST")


def _breakout_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only bounded model metadata; per-player labels stay on candidate cards."""

    return {
        "status": "available" if result.get("status") == "available" else "unavailable",
        "method": str(result.get("method") or "")[:300],
        "calibrated": False,
        "coveragePositions": [
            str(position)[:16]
            for position in result.get("coveragePositions", [])[:3]
            if isinstance(position, str)
        ],
        "evidencePlayers": max(0, min(int(_number(result.get("evidencePlayers"), 0)), 500)),
        "message": str(result.get("message") or "")[:300],
    }


def _rounded_normalized_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Round normalized weights while keeping their serialized total at one."""
    rounded = {key: round(value, 6) for key, value in weights.items()}
    active = [key for key, value in rounded.items() if value > 0]
    if not active:
        return rounded

    residual = round(1.0 - sum(rounded.values()), 6)
    if residual:
        target = max(active, key=lambda key: rounded[key])
        rounded[target] = round(rounded[target] + residual, 6)
    return rounded


def _strategy_weights(strategy: str, *, risk_available: bool) -> dict[str, float]:
    weights = dict(_STRATEGY_WEIGHTS.get(strategy, _STRATEGY_WEIGHTS["balanced"]))
    if not risk_available:
        weights["riskNews"] = 0.0
    total = sum(weights.values())
    if total <= 0:
        return weights
    return {key: value / total for key, value in weights.items()}


def _strategy_score(
    scores: Mapping[str, float | None], strategy: str, *, risk_available: bool
) -> tuple[float, dict[str, float]]:
    weights = _strategy_weights(strategy, risk_available=risk_available)
    if scores.get("riskNews") is None and weights.get("riskNews", 0) > 0:
        weights["riskNews"] = 0.0
        total = sum(weights.values())
        weights = {key: value / total for key, value in weights.items()}
    score = sum(
        value * weights[key]
        for key, value in scores.items()
        if value is not None and key in weights
    )
    return score, weights


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
# Yahoo league creation and the local profile contract both top out at 20 teams.
# Enforcing the same ceiling here bounds snake-turn and simulation work even when
# an upstream league payload is malformed.
_MAX_TEAM_COUNT = 20


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _optional_positive_number(value: Any) -> float | None:
    """Return an explicitly supplied positive finite number, never a fallback."""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _bounded_nonnegative_number(value: Any, maximum: float) -> float | None:
    if type(value) not in (int, float):
        return None
    result = float(value)
    return result if math.isfinite(result) and 0 <= result <= maximum else None


def _safe_evidence_time(value: Any, *, optional: bool = False) -> tuple[bool, str | None]:
    if value is None and optional:
        return True, None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 40
        or value != value.strip()
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        return False, None
    if _parse_time(value) is None:
        return False, None
    return True, value


def _fantasypros_projection_evidence(
    value: Mapping[str, Any], position: str
) -> dict[str, Any] | None:
    """Allowlist provider projection evidence and optional matched experience."""

    season = value.get("projection_season")
    scoring = value.get("projection_scoring")
    stale = value.get("projection_stale")
    points = _bounded_nonnegative_number(value.get("projected_points"), 1_000.0)
    opportunities = _bounded_nonnegative_number(value.get("projected_opportunities"), 1_000.0)
    opportunity_kind = value.get("projection_opportunity_kind")
    valid_kinds = {
        "RB": {"touches"},
        "WR": {"targets", "receptions"},
        "TE": {"targets", "receptions"},
    }
    fetched_valid, fetched_at = _safe_evidence_time(value.get("projection_fetched_at"))
    source_valid, source_as_of = _safe_evidence_time(
        value.get("projection_source_as_of"), optional=True
    )
    if (
        value.get("projection_source") != "FantasyPros"
        or type(season) is not int
        or not 2012 <= season <= 2100
        or scoring not in {"STD", "HALF", "PPR"}
        or type(stale) is not bool
        or points is None
        or opportunities is None
        or opportunity_kind not in valid_kinds.get(position, set())
        or not fetched_valid
        or not source_valid
    ):
        return None
    result = {
        "source": "FantasyPros",
        "season": season,
        "scoring": scoring,
        "sourceAsOf": source_as_of,
        "fetchedAt": fetched_at,
        "stale": stale,
        "projectedPoints": points,
        "projectedOpportunities": opportunities,
        "opportunityKind": opportunity_kind,
    }
    experience = value.get("experience_years")
    experience_source = value.get("experience_source")
    if type(experience) is int and 0 <= experience <= 30 and experience_source == "Sleeper":
        result["experienceYears"] = experience
        result["experienceSource"] = "Sleeper"
    return result


def _position(value: Any) -> str:
    result = str(value or "").strip().upper()
    return _POSITION_ALIASES.get(result, result)


def _nfl_team(value: Any) -> str:
    result = str(value or "").strip().upper()
    return _NFL_TEAM_ALIASES.get(result, result)


@lru_cache(maxsize=2048)
def _cached_name_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", normalized.lower())
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return tuple(tokens)


def _name_tokens(value: Any) -> tuple[str, ...]:
    return _cached_name_tokens(str(value or ""))


def _inverse_logistic(exponent: float) -> float:
    """Return 1/(1+exp(exponent)) without overflowing on malformed ranking data."""

    if exponent >= 60.0:
        return 0.0
    if exponent <= -60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def _same_player(pick: Mapping[str, Any], player: Mapping[str, Any]) -> bool:
    """Resolve Yahoo's initialed ledger names without broad last-name-only matching."""

    pick_player_key = normalize_yahoo_player_key(pick.get("playerKey") or pick.get("player_key"))
    ranking_player_key = normalize_yahoo_player_key(
        player.get("player_key") or player.get("playerKey")
    )
    if pick_player_key and ranking_player_key:
        return pick_player_key == ranking_player_key

    pick_position = _position(pick.get("position"))
    player_position = _position(player.get("position"))
    pick_team = _nfl_team(pick.get("nflTeam"))
    player_team = _nfl_team(player.get("team"))
    if (
        pick_position == "DST"
        and player_position == "DST"
        and pick_team
        and pick_team == player_team
    ):
        return True

    left = _name_tokens(pick.get("player"))
    right = _name_tokens(player.get("name"))
    if not left or not right:
        return False
    if left == right:
        return True
    if left[-1] != right[-1] or left[0][0] != right[0][0]:
        return False
    # Initialed names are too ambiguous without agreement on both metadata fields.
    return bool(
        pick_position
        and player_position
        and pick_position == player_position
        and pick_team
        and player_team
        and pick_team == player_team
    )


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _snake_pick(round_number: int, draft_slot: int, team_count: int) -> int:
    pick_in_round = draft_slot if round_number % 2 else team_count - draft_slot + 1
    return (round_number - 1) * team_count + pick_in_round


def reconcile_live_draft(
    context: Mapping[str, Any], team_count: int | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Derive trustworthy draft position and health metadata from a browser ledger."""

    raw_picks = context.get("picks", [])
    picks = (
        [dict(pick) for pick in raw_picks if isinstance(pick, Mapping)]
        if isinstance(raw_picks, list)
        else []
    )
    numbered = [
        int(p["pickNumber"])
        for p in picks
        if isinstance(p.get("pickNumber"), int) and p["pickNumber"] > 0
    ]
    unnumbered_count = len(picks) - len(numbered)
    counts = Counter(numbered)
    duplicate_numbers = sorted(number for number, amount in counts.items() if amount > 1)
    latest = max(numbered, default=0)
    missing = sorted(set(range(1, latest + 1)) - set(numbered))
    current = latest + 1

    fantasy_teams = {str(p.get("fantasyTeam")) for p in picks if p.get("fantasyTeam")}
    if team_count:
        resolved_team_count = int(team_count)
        team_count_source = "league"
    elif len(fantasy_teams) >= 2:
        resolved_team_count = len(fantasy_teams)
        team_count_source = "ledger"
    else:
        resolved_team_count = 12
        team_count_source = "default"
    if resolved_team_count < 2:
        resolved_team_count = 12
        team_count_source = "default"
    elif resolved_team_count > _MAX_TEAM_COUNT:
        resolved_team_count = _MAX_TEAM_COUNT
        team_count_source = f"{team_count_source}-clamped"

    user_picks = [
        p
        for p in picks
        if p.get("isUserPick") is True or str(p.get("fantasyTeam", "")).lower() == "your team"
    ]
    user_slot = None
    for pick in sorted(user_picks, key=lambda item: item.get("pickNumber", 10_000)):
        pick_number = pick.get("pickNumber")
        if isinstance(pick_number, int):
            round_number = ((pick_number - 1) // resolved_team_count) + 1
            in_round = ((pick_number - 1) % resolved_team_count) + 1
            user_slot = in_round if round_number % 2 else resolved_team_count - in_round + 1
            break

    next_user_pick = None
    if user_slot is not None:
        start_round = max(1, ((current - 1) // resolved_team_count) + 1)
        for round_number in range(start_round, start_round + 4):
            candidate = _snake_pick(round_number, user_slot, resolved_team_count)
            if candidate >= current:
                next_user_pick = candidate
                break

    generated = _parse_time(context.get("generatedAt"))
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (reference - generated).total_seconds()) if generated else None
    authoritative_capture_blocked = context.get("captureBlocked") is True
    warnings: list[str] = []
    if team_count_source.endswith("-clamped"):
        warnings.append(
            f"Team count exceeded the supported maximum of {_MAX_TEAM_COUNT} and was "
            "clamped; snake-turn projections may be inaccurate"
        )
    elif team_count_source != "league":
        warnings.append(
            f"Team count was inferred from {team_count_source}; snake-turn projections may be inaccurate"
        )
    if unnumbered_count:
        warnings.append(f"Pick ledger has {unnumbered_count} unnumbered pick(s)")
    if missing:
        warnings.append(f"Numbered pick ledger has gaps: {missing}")
    if duplicate_numbers:
        warnings.append(f"Numbered pick ledger has duplicates: {duplicate_numbers}")
    if authoritative_capture_blocked:
        warnings.append(
            "Authoritative Yahoo ledger capture integrity is unresolved; "
            "recommendations remain blocked until a coherent Round-by-Round scan or repair"
        )
    if not user_picks:
        warnings.append("No user picks have been identified yet")
    if generated is None:
        warnings.append("Draft state has no valid generated timestamp")
    elif age_seconds is not None and age_seconds > 120:
        warnings.append(f"Draft state is stale by {int(age_seconds)} seconds")

    return {
        "sessionKey": (
            context.get("draft", {}).get("sessionKey")
            if isinstance(context.get("draft"), Mapping)
            else None
        ),
        "generatedAt": context.get("generatedAt"),
        "teamCount": resolved_team_count,
        "currentOverallPick": current,
        "userDraftSlot": user_slot,
        "nextUserPick": next_user_pick,
        "picksUntilUserTurn": (
            max(0, next_user_pick - current) if next_user_pick is not None else None
        ),
        "userRoster": user_picks,
        "picks": picks,
        "health": {
            "complete": (
                not missing
                and not duplicate_numbers
                and unnumbered_count == 0
                and not authoritative_capture_blocked
            ),
            "authoritativeCaptureBlocked": authoritative_capture_blocked,
            "fresh": generated is not None and age_seconds is not None and age_seconds <= 120,
            "teamCountSource": team_count_source,
            "missingPickNumbers": missing,
            "unnumberedPickCount": unnumbered_count,
            "duplicatePickNumbers": duplicate_numbers,
            "stateAgeSeconds": round(age_seconds, 3) if age_seconds is not None else None,
            "warnings": warnings,
        },
    }


def _roster_positions(league_info: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = league_info.get("roster_positions")
    if not isinstance(raw, list):
        raw = league_info.get("rosterPositions")
    if not isinstance(raw, list) or not raw:
        return [dict(item) for item in _DEFAULT_ROSTER]
    result = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        name = _position(entry.get("position") or entry.get("position_type"))
        count = max(0, int(_number(entry.get("count"), 1)))
        result.append(
            {"position": name, "count": count, "position_type": entry.get("position_type")}
        )
    return result or [dict(item) for item in _DEFAULT_ROSTER]


@dataclass(frozen=True)
class Candidate:
    raw: dict[str, Any]
    name: str
    position: str
    team: str
    rank: float
    adp: float | None

    @property
    def effective_adp(self) -> float:
        """Keep legacy scoring bounded while preserving whether market ADP existed."""

        return self.adp if self.adp is not None else self.rank

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], fallback_rank: int) -> Candidate | None:
        name = str(value.get("name") or "").strip()
        position = _position(value.get("position"))
        if not name or not position:
            return None
        rank = _number(value.get("rank"), fallback_rank)
        raw_adp = value.get("average_draft_position")
        if raw_adp is None:
            raw_adp = value.get("adp")
        adp = _optional_positive_number(raw_adp)
        return cls(dict(value), name, position, str(value.get("team") or ""), rank, adp)


class PlayerValueAgent:
    name = "playerValue"

    def score(
        self, candidate: Candidate, current_pick: int, pool_size: int
    ) -> tuple[float, dict[str, Any]]:
        rank_quality = max(0.0, 100.0 - ((candidate.rank - 1) / max(1, pool_size)) * 100.0)
        adp_delta = current_pick - candidate.adp if candidate.adp is not None else None
        effective_delta = current_pick - candidate.effective_adp
        adp_value = max(0.0, min(100.0, 50.0 + effective_delta * 4.0))
        score = 0.72 * rank_quality + 0.28 * adp_value
        if candidate.rank <= 12:
            tier = "elite"
        elif candidate.rank <= 36:
            tier = "starter"
        elif candidate.rank <= 84:
            tier = "depth"
        else:
            tier = "late"
        return score, {
            "rank": candidate.rank,
            "adp": candidate.adp,
            "adpAvailable": candidate.adp is not None,
            "adpDelta": round(adp_delta, 2) if adp_delta is not None else None,
            "adpBasis": "real-market-adp" if candidate.adp is not None else "rank-fallback",
            "tier": tier,
        }


class RosterConstructionAgent:
    name = "rosterConstruction"

    def __init__(
        self, roster: Sequence[Mapping[str, Any]], roster_positions: Sequence[Mapping[str, Any]]
    ):
        self.counts = Counter(_position(player.get("position")) for player in roster)
        self.slots = list(roster_positions)
        self.required = Counter()
        self.flex_count = 0
        self.superflex_count = 0
        self.total_slots = 0
        for slot in self.slots:
            name = _position(slot.get("position"))
            count = int(slot.get("count", 0))
            self.total_slots += count
            if name == "FLEX":
                self.flex_count += count
            elif name == "SUPERFLEX":
                self.superflex_count += count
            elif name not in {"BN", "IR", "TAXI"}:
                self.required[name] += count

    def score(self, candidate: Candidate) -> tuple[float, dict[str, Any]]:
        position = candidate.position
        current = self.counts[position]
        required = self.required[position]
        if position == "QB" and self.superflex_count:
            required += self.superflex_count
        if position in {"RB", "WR", "TE"} and self.flex_count:
            flex_share = max(1, math.ceil(self.flex_count / 2))
            required += flex_share

        useful_targets = {
            "QB": max(2, required),
            "RB": max(4, required + 2),
            "WR": max(5, required + 2),
            "TE": max(2, required),
            "K": 1,
            "DST": 1,
        }
        target = useful_targets.get(position, max(1, required))
        if current < required:
            score = 98.0 if required - current > 1 else 90.0
            impact = f"fills an unfilled {position} starter or flex requirement"
        elif current < target:
            score = 62.0
            impact = f"adds useful {position} depth ({current}/{target} target)"
        else:
            score = 18.0
            impact = f"adds beyond the current {position} depth target"
        if position == "QB" and self.superflex_count and current < required:
            score = min(100.0, score + 10.0)
            impact = "fills a high-value QB/Superflex requirement"
        return score, {
            "positionCount": current,
            "required": required,
            "target": target,
            "impact": impact,
        }

    def summary(self) -> dict[str, Any]:
        slots: list[dict[str, Any]] = []
        for position, required in self.required.items():
            current = min(self.counts[position], required)
            slots.append(
                {
                    "position": position,
                    "current": current,
                    "required": required,
                    "open": max(0, required - current),
                }
            )

        eligible_surplus = sum(
            max(0, self.counts[position] - self.required[position])
            for position in ("RB", "WR", "TE")
        )
        if self.flex_count:
            current = min(self.flex_count, eligible_surplus)
            eligible_surplus -= current
            slots.append(
                {
                    "position": "FLEX",
                    "current": current,
                    "required": self.flex_count,
                    "open": self.flex_count - current,
                }
            )

        if self.superflex_count:
            superflex_surplus = eligible_surplus + max(0, self.counts["QB"] - self.required["QB"])
            current = min(self.superflex_count, superflex_surplus)
            slots.append(
                {
                    "position": "SUPERFLEX",
                    "current": current,
                    "required": self.superflex_count,
                    "open": self.superflex_count - current,
                }
            )

        open_starters = sum(slot["open"] for slot in slots)
        draftable_slots = sum(
            max(0, int(slot.get("count", 0)))
            for slot in self.slots
            if _position(slot.get("position")) not in {"IR", "TAXI"}
        )
        return {
            "slots": slots,
            "openStarterSlots": open_starters,
            "starterComplete": open_starters == 0,
            "draftableRosterSlots": draftable_slots,
            "positionCounts": {
                position: count for position, count in sorted(self.counts.items()) if position
            },
        }


class DraftPlanAgent:
    """Apply a bounded, round-aware RB/WR construction preference."""

    name = "draftPlan"

    def __init__(self, roster: Sequence[Mapping[str, Any]], plan: str):
        self.counts = Counter(_position(player.get("position")) for player in roster)
        self.round = len(roster) + 1
        self.plan = plan if plan in _DRAFT_PLANS else "balanced_rb_wr"

    def score(self, candidate: Candidate) -> tuple[float, dict[str, Any]]:
        position = candidate.position
        score = 50.0
        impact = "uses best available value without an RB/WR construction preference"

        if self.plan == "balanced_rb_wr" and self.round <= 4:
            if position in {"RB", "WR"}:
                other = "WR" if position == "RB" else "RB"
                position_deficit = max(0, 2 - self.counts[position])
                other_deficit = max(0, 2 - self.counts[other])
                score = 94.0 if position_deficit > other_deficit else 84.0
                if self.round == 3 and self.counts[position] == 0:
                    score = 100.0
                if position_deficit == 0 and other_deficit > 0:
                    score = 24.0
                impact = "works toward two RBs and two WRs through Round 4"
            else:
                score = 24.0
                impact = "defers non-RB/WR picks while building the early RB/WR core"
        elif self.plan == "hero_rb" and self.round <= 5:
            if self.counts["RB"] == 0 and self.round <= 2:
                score = 100.0 if position == "RB" else (74.0 if position == "WR" else 28.0)
                impact = "seeks one anchor RB in the first two rounds"
            elif position == "WR":
                score = 98.0 if self.counts["WR"] < 3 else 78.0
                impact = "builds WR strength around the anchor RB through Round 5"
            elif position == "RB":
                score = 20.0
                impact = "defers the second RB after securing the anchor"
            else:
                score = 40.0
                impact = "keeps the early focus on one anchor RB plus WR depth"
        elif self.plan == "wr_heavy":
            if self.round <= 4:
                if position == "WR":
                    score = 100.0 if self.counts["WR"] < 3 else 82.0
                    impact = "builds a WR-heavy core through Round 4"
                elif position == "RB":
                    score = 18.0
                    impact = "defers RB in the Zero-RB opening"
                else:
                    score = 52.0
                    impact = "allows elite non-RB value without displacing the WR focus"
            elif self.round <= 7 and self.counts["RB"] < 2:
                score = 94.0 if position == "RB" else 52.0
                impact = "attacks RB upside after the WR-heavy opening"
        elif self.plan == "rb_heavy" and self.round <= 5:
            target = 2 if self.round <= 3 else 3
            if position == "RB":
                score = 100.0 if self.counts["RB"] < target else 70.0
                target_round = 3 if self.round <= 3 else 5
                impact = f"works toward {target} RBs by Round {target_round}"
            elif position == "WR":
                score = 68.0
                impact = "adds WR value without abandoning the RB-heavy opening"
            else:
                score = 26.0
                impact = "defers non-RB/WR picks during the RB-heavy opening"

        return score, {
            "plan": self.plan,
            "round": self.round,
            "positionCount": self.counts[position],
            "impact": impact,
        }


class DraftDynamicsAgent:
    name = "draftDynamics"

    def __init__(self, picks: Sequence[Mapping[str, Any]]):
        recent = list(picks)[-8:]
        self.recent_counts = Counter(_position(pick.get("position")) for pick in recent)

    def score(self, candidate: Candidate) -> tuple[float, dict[str, Any]]:
        recent = self.recent_counts[candidate.position]
        run = recent >= 3
        score = min(82.0, 46.0 + recent * 8.0)
        return score, {"recentPositionPicks": recent, "window": 8, "runDetected": run}


class OpponentModelAgent:
    name = "opponentModel"

    def score(
        self, candidate: Candidate, next_user_pick: int | None
    ) -> tuple[float, dict[str, Any]]:
        if next_user_pick is None:
            probability = 0.5
            basis = "unknown user draft slot"
        else:
            probability = _inverse_logistic((next_user_pick - candidate.effective_adp) / 6.0)
            basis = (
                "heuristic from real ADP and picks until the next user turn"
                if candidate.adp is not None
                else "rank fallback because real ADP is unavailable"
            )
        urgency = (1.0 - probability) * 100.0
        return urgency, {
            "returnProbability": round(probability, 4),
            "basis": basis,
            "calibrated": False,
        }


class RiskNewsAgent:
    name = "riskNews"
    _INJURY_MAX_AGE_SECONDS = 86_400.0
    _NEWS_MAX_AGE_SECONDS = 172_800.0
    _SCORES = {
        "healthy": 82.0,
        "probable": 74.0,
        "questionable": 42.0,
        "doubtful": 18.0,
        "out": 0.0,
        "ir": 0.0,
        "pup": 8.0,
        "nfi": 8.0,
        "not active": 8.0,
        "suspended": 12.0,
        "day-to-day": 48.0,
    }
    _NEWS_SCORES = {
        "injury": 48.0,
        "injuries": 48.0,
        "transaction": 56.0,
    }

    def __init__(self, reference_time: datetime | None = None):
        now = reference_time or datetime.now(timezone.utc)
        self.reference_time = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    def _recent(self, value: Any, maximum_age_seconds: float) -> bool:
        parsed = _parse_time(value)
        if parsed is None:
            return False
        age = (self.reference_time.astimezone(timezone.utc) - parsed).total_seconds()
        return -300.0 <= age <= maximum_age_seconds

    def _fresh_signal(
        self, candidate: Candidate, prefix: str
    ) -> tuple[bool, str | None, str | None]:
        source_value = candidate.raw.get(f"{prefix}_source")
        updated_value = candidate.raw.get(f"{prefix}_updated_at")
        source = str(source_value).strip()[:80] if isinstance(source_value, str) else ""
        updated_at = (
            updated_value[:40]
            if isinstance(updated_value, str) and _parse_time(updated_value) is not None
            else None
        )
        if prefix == "injury":
            freshness_value = candidate.raw.get("injury_snapshot_at") or candidate.raw.get(
                "retrievedAt"
            )
            maximum_age = self._INJURY_MAX_AGE_SECONDS
        else:
            freshness_value = updated_at
            maximum_age = self._NEWS_MAX_AGE_SECONDS
        fresh = bool(
            candidate.raw.get(f"{prefix}_fresh") is True
            and source
            and updated_at
            and self._recent(freshness_value, maximum_age)
        )
        return fresh, source or None, updated_at

    def _recent_news(self, candidate: Candidate, *, fresh: bool) -> list[dict[str, str]]:
        if not fresh or not isinstance(candidate.raw.get("recentNews"), list):
            return []
        result: list[dict[str, str]] = []
        for raw in candidate.raw["recentNews"][:3]:
            if not isinstance(raw, Mapping):
                continue
            headline = str(raw.get("headline") or "").strip()[:240]
            published = str(raw.get("publishedAt") or "").strip()[:40]
            if not headline or not self._recent(published, self._NEWS_MAX_AGE_SECONDS):
                continue
            item = {"headline": headline, "publishedAt": published}
            category = str(raw.get("category") or "").strip()[:80]
            if category:
                item["category"] = category
            result.append(item)
        return result

    def injury_available(self, candidate: Candidate) -> bool:
        fresh, _source, _updated_at = self._fresh_signal(candidate, "injury")
        raw_status = candidate.raw.get("injury_status") or candidate.raw.get("status")
        status = str(raw_status).strip().lower() if raw_status else "unknown"
        return fresh and status in self._SCORES

    def news_available(self, candidate: Candidate) -> bool:
        fresh, _source, _updated_at = self._fresh_signal(candidate, "news")
        return fresh and bool(self._recent_news(candidate, fresh=fresh))

    def score(self, candidate: Candidate) -> tuple[float | None, dict[str, Any]]:
        injury_fresh, injury_source, injury_updated_at = self._fresh_signal(candidate, "injury")
        news_fresh, news_source, news_updated_at = self._fresh_signal(candidate, "news")
        raw_status = candidate.raw.get("injury_status") or candidate.raw.get("status")
        supplied_status = str(raw_status).strip().lower() if raw_status else "unknown"
        injury_available = injury_fresh and supplied_status in self._SCORES
        status = supplied_status if injury_available else "unknown"
        recent_news = self._recent_news(candidate, fresh=news_fresh)
        news_scores = [
            self._NEWS_SCORES[str(item.get("category") or "").casefold()]
            for item in recent_news
            if str(item.get("category") or "").casefold() in self._NEWS_SCORES
        ]
        score = self._SCORES[status] if injury_available else min(news_scores, default=None)
        available = score is not None
        basis = (
            "injury-status"
            if injury_available
            else ("recent-news-category" if news_scores else "unknown")
        )
        return score, {
            "status": status,
            "source": injury_source or news_source,
            "updatedAt": injury_updated_at or news_updated_at,
            "fresh": injury_fresh or news_fresh,
            "available": available,
            "basis": basis,
            "injuryFresh": injury_fresh,
            "newsFresh": news_fresh,
            "recentNews": recent_news,
        }


class ScenarioSimulatorAgent:
    name = "scenarioSimulator"

    def __init__(self, simulations: int, random_seed: int):
        self.simulations = max(0, min(int(simulations), 512))
        self.random_seed = int(random_seed)

    def score(
        self, candidate: Candidate, current_pick: int, next_user_pick: int | None
    ) -> tuple[float, dict[str, Any]]:
        intervening = max(0, (next_user_pick or current_pick) - current_pick)
        if self.simulations == 0 or intervening == 0:
            survival = 1.0 if intervening == 0 else 0.5
        else:
            stable_seed = int(hashlib.sha256(candidate.name.encode("utf-8")).hexdigest()[:8], 16)
            rng = random.Random(self.random_seed + stable_seed)
            survived = 0
            for _ in range(self.simulations):
                selected = False
                for offset in range(intervening):
                    pick_number = current_pick + offset
                    pressure = _inverse_logistic((candidate.effective_adp - pick_number) / 5.0)
                    hazard = min(0.72, 0.035 + pressure * 0.22)
                    if rng.random() < hazard:
                        selected = True
                        break
                if not selected:
                    survived += 1
            survival = survived / self.simulations
        return (1.0 - survival) * 100.0, {
            "survivalProbability": round(survival, 4),
            "simulations": self.simulations,
            "seed": self.random_seed,
            "calibrated": False,
        }


class RecommendationCritic:
    """Invariant and sensitivity checks; intentionally not an LLM."""

    def review(
        self,
        recommendations: Sequence[Mapping[str, Any]],
        state: Mapping[str, Any],
        rankings_present: bool,
        unresolved_drafted: int,
    ) -> dict[str, Any]:
        drafted = state.get("picks", [])
        available = all(
            not any(_same_player(pick, item.get("player", {})) for pick in drafted)
            for item in recommendations
        )
        finite_scores = all(
            math.isfinite(float(item.get("overallScore", math.nan))) for item in recommendations
        )
        health = state.get("health", {})
        checks = {
            "ledgerComplete": bool(health.get("complete")),
            "stateFresh": bool(health.get("fresh")),
            "teamCountAuthoritative": health.get("teamCountSource") == "league",
            "rankingPoolPresent": rankings_present,
            "allDraftedPlayersResolved": unresolved_drafted == 0,
            "allRecommendationsAvailable": available,
            "scoresFinite": finite_scores,
            "deterministicCritic": True,
        }
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "method": "deterministic invariants; no LLM self-review",
        }


class LiveDraftRecommendationEngine:
    """Coordinate specialist scores and return one draft-clock-safe answer."""

    def __init__(self, *, simulations: int = 256, random_seed: int = 2026):
        self.simulations = max(0, min(int(simulations), 512))
        self.random_seed = random_seed

    def recommend(
        self,
        live_context: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        league_info: Mapping[str, Any] | None = None,
        *,
        strategy: str = "balanced",
        draft_plan: str = "balanced_rb_wr",
        count: int = 5,
        now: datetime | None = None,
        market_source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        league = dict(league_info or {})
        team_count = int(_number(league.get("teams") or league.get("num_teams"), 0)) or None
        reference_time = now or datetime.now(timezone.utc)
        state = reconcile_live_draft(live_context, team_count=team_count, now=reference_time)
        warnings = list(state["health"]["warnings"])
        risk_agent = RiskNewsAgent(reference_time)
        ranking_candidates = [
            Candidate.from_mapping(item, index) if isinstance(item, Mapping) else None
            for index, item in enumerate(rankings, start=1)
        ]
        roster_agent = RosterConstructionAgent(state["userRoster"], _roster_positions(league))
        draft_plan_agent = DraftPlanAgent(state["userRoster"], draft_plan)
        injury_available = any(
            risk_agent.injury_available(candidate)
            for candidate in ranking_candidates
            if candidate is not None
        )
        news_available = any(
            risk_agent.news_available(candidate)
            for candidate in ranking_candidates
            if candidate is not None
        )
        risk_available = any(
            risk_agent.score(candidate)[0] is not None
            for candidate in ranking_candidates
            if candidate is not None
        )
        _, initial_market_signals = build_market_decision_payload(
            state,
            [],
            source=market_source,
            target_season=reference_time.year,
            ranking_rows=len(rankings),
            drafted_count=0,
            unresolved_drafted=0,
            counts_trustworthy=state["health"]["complete"],
        )
        breakout_evaluation = evaluate_breakout_watch(rankings, now=reference_time)
        breakout_labels = breakout_evaluation["labels"]
        breakout_summary = _breakout_summary(breakout_evaluation)
        base = {
            "source": "live-draft-specialist-suite",
            "strategy": strategy if strategy in _STRATEGY_WEIGHTS else "balanced",
            "draftPlan": draft_plan_agent.plan,
            "generatedAt": live_context.get("generatedAt"),
            "state": state,
            "capabilities": {
                "externalNews": news_available,
                "injuryStatus": injury_available,
                "opponentModel": "heuristic",
                "scenarioSimulation": self.simulations > 0,
                "llmOnRequestPath": False,
                "breakoutWatch": breakout_summary["status"] == "available",
            },
            "warnings": warnings,
            "marketSignals": initial_market_signals,
            "nextTwoPicksPlan": plan_next_two_picks([], state),
            "cockpit": self._cockpit(
                state,
                rankings,
                league,
                roster_agent,
                [],
                breakout_watch=breakout_summary,
                risk_available=risk_available,
                blocked=not state["health"]["complete"],
            ),
        }
        if not state["health"]["complete"]:
            if state["health"]["authoritativeCaptureBlocked"]:
                warnings.append(
                    "Recommendation blocked because authoritative ledger capture integrity "
                    "is unresolved and drafted-player availability is uncertain"
                )
            else:
                warnings.append(
                    "Recommendation blocked because gaps, duplicate pick numbers, or unnumbered "
                    "picks make availability uncertain"
                )
            return self._empty_result(base, "blocked", self._draft_advice(state), started)
        if not rankings:
            warnings.append(
                "No ranking pool is available; player recommendations were not invented"
            )
            return self._empty_result(base, "degraded", self._draft_advice(state), started)

        valid_candidates = [
            (index, candidate)
            for index, candidate in enumerate(ranking_candidates)
            if candidate is not None
        ]
        candidates: list[Candidate] = []
        drafted_count = 0
        candidate_breakout_labels: list[dict[str, Any] | None] = []
        unresolved_drafted: list[str] = []
        for index, candidate in valid_candidates:
            if any(_same_player(pick, candidate.raw) for pick in state["picks"]):
                drafted_count += 1
                continue
            candidates.append(candidate)
            raw_label = breakout_labels[index] if index < len(breakout_labels) else None
            candidate_breakout_labels.append(
                dict(raw_label) if isinstance(raw_label, Mapping) else None
            )
        for pick in state["picks"]:
            if not any(_same_player(pick, raw) for raw in rankings if isinstance(raw, Mapping)):
                unresolved_drafted.append(str(pick.get("player") or "unknown"))
        if unresolved_drafted:
            warnings.append(
                f"{len(unresolved_drafted)} drafted player(s) could not be resolved against rankings"
            )
        if not candidates:
            warnings.append("Every ranking entry was drafted or invalid")
            _, base["marketSignals"] = build_market_decision_payload(
                state,
                [],
                source=market_source,
                target_season=reference_time.year,
                ranking_rows=len(rankings),
                drafted_count=drafted_count,
                unresolved_drafted=len(unresolved_drafted),
            )
            return self._empty_result(base, "degraded", self._draft_advice(state), started)

        dynamics_agent = DraftDynamicsAgent(state["picks"])
        value_agent = PlayerValueAgent()
        opponent_agent = OpponentModelAgent()
        scenario_agent = ScenarioSimulatorAgent(self.simulations, self.random_seed)
        weights = _strategy_weights(base["strategy"], risk_available=risk_available)
        pool_size = max(len(rankings), 100)
        evaluated = []

        for candidate, breakout_label in zip(candidates, candidate_breakout_labels, strict=True):
            value, value_detail = value_agent.score(
                candidate, state["currentOverallPick"], pool_size
            )
            roster, roster_detail = roster_agent.score(candidate)
            plan, plan_detail = draft_plan_agent.score(candidate)
            dynamics, dynamics_detail = dynamics_agent.score(candidate)
            opponent, opponent_detail = opponent_agent.score(candidate, state["nextUserPick"])
            risk, risk_detail = risk_agent.score(candidate)
            scenario, scenario_detail = scenario_agent.score(
                candidate, state["currentOverallPick"], state["nextUserPick"]
            )
            scores = {
                "value": value,
                "rosterConstruction": roster,
                "draftPlan": plan,
                "draftDynamics": dynamics,
                "opponentModel": opponent,
                "riskNews": risk,
                "scenario": scenario,
            }
            overall, effective_weights = _strategy_score(
                scores,
                base["strategy"],
                risk_available=risk_available,
            )
            reasoning = self._reasoning(
                candidate,
                value_detail,
                roster_detail,
                plan_detail,
                dynamics_detail,
                risk_detail,
                opponent_detail,
            )
            evaluated_candidate = {
                "player": {
                    "name": candidate.name,
                    "position": candidate.position,
                    "team": candidate.team,
                    **(
                        {"playerKey": yahoo_player_key}
                        if (
                            yahoo_player_key := normalize_yahoo_player_key(
                                candidate.raw.get("player_key") or candidate.raw.get("playerKey")
                            )
                        )
                        else {}
                    ),
                    "rank": candidate.rank,
                    "adp": candidate.adp,
                    "adpAvailable": candidate.adp is not None,
                    "byeWeek": candidate.raw.get("bye") or candidate.raw.get("bye_week"),
                },
                "overallScore": round(overall, 2),
                "scores": {
                    key: round(value, 2) if value is not None else None
                    for key, value in scores.items()
                },
                "effectiveWeights": _rounded_normalized_weights(effective_weights),
                "returnProbability": opponent_detail["returnProbability"],
                "rosterImpact": roster_detail["impact"],
                "reasoning": reasoning,
                "risk": risk_detail,
                "specialistDetails": {
                    "value": value_detail,
                    "rosterConstruction": roster_detail,
                    "draftPlan": plan_detail,
                    "draftDynamics": dynamics_detail,
                    "opponentModel": opponent_detail,
                    "scenario": scenario_detail,
                },
            }
            if isinstance(breakout_label, Mapping):
                evaluated_candidate["breakoutWatch"] = dict(breakout_label)
            projection_evidence = _fantasypros_projection_evidence(
                candidate.raw, candidate.position
            )
            if projection_evidence is not None:
                evaluated_candidate["projectionEvidence"] = projection_evidence
            evaluated.append(evaluated_candidate)

        evaluated, base["marketSignals"] = build_market_decision_payload(
            state,
            evaluated,
            source=market_source,
            target_season=reference_time.year,
            ranking_rows=len(rankings),
            drafted_count=drafted_count,
            unresolved_drafted=len(unresolved_drafted),
        )

        evaluated.sort(
            key=lambda item: (-item["overallScore"], item["player"]["rank"], item["player"]["name"])
        )
        selected = evaluated[: max(1, min(int(count), 20))]
        margin = (
            selected[0]["overallScore"] - selected[1]["overallScore"] if len(selected) > 1 else 10.0
        )
        for item in selected:
            data_quality = (
                0.92
                if not risk_available
                else (0.88 if item["risk"]["available"] is False else 0.96)
            )
            confidence = (
                0.45 + item["overallScore"] / 200.0 + min(0.08, max(0.0, margin) / 100.0)
            ) * data_quality
            item["confidence"] = round(max(0.0, min(1.0, confidence)), 3)
            item["confidenceCalibrated"] = False

        critic = RecommendationCritic().review(
            selected, state, bool(rankings), len(unresolved_drafted)
        )
        next_two_plan = plan_next_two_picks(
            evaluated, state, unresolved_drafted=len(unresolved_drafted)
        )
        base["capabilities"]["breakoutWatch"] = breakout_summary["status"] == "available"
        result = {
            **base,
            "status": "success" if critic["passed"] else "degraded",
            "primaryRecommendation": selected[0],
            "alternatives": selected[1:3],
            "recommendations": selected,
            "nextTwoPicksPlan": next_two_plan,
            "appliedWeights": _rounded_normalized_weights(weights),
            "contingency": {
                "ifPrimaryUnavailable": (
                    selected[1]["player"]["name"]
                    if len(selected) > 1
                    else "Re-run after the next synced pick"
                ),
                "atNextTurn": self._next_turn_plan(selected),
            },
            "specialists": {
                "liveStateReconciler": state["health"],
                "playerValue": "rank, ADP value, and tiers",
                "rosterConstruction": "starter, flex, Superflex, and depth requirements",
                "draftPlan": "round-aware RB/WR roster construction preference",
                "draftDynamics": "recent positional-run pressure",
                "opponentModel": "uncalibrated ADP survival heuristic",
                "riskNews": "sourced injury/news when supplied; missing means unknown",
                "scenarioSimulator": {"simulations": self.simulations, "seed": self.random_seed},
                "recommendationCritic": "availability, completeness, and finite-score invariants",
            },
            "critic": critic,
            "draftAdvice": self._draft_advice(state),
            "cockpit": self._cockpit(
                state,
                rankings,
                league,
                roster_agent,
                evaluated,
                breakout_watch=breakout_summary,
                risk_available=risk_available,
                blocked=False,
            ),
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 3),
        }
        return result

    @staticmethod
    def _reasoning(
        candidate: Candidate,
        value: Mapping[str, Any],
        roster: Mapping[str, Any],
        draft_plan: Mapping[str, Any],
        dynamics: Mapping[str, Any],
        risk: Mapping[str, Any],
        opponent: Mapping[str, Any],
    ) -> list[str]:
        market_context = (
            f"real ADP {candidate.adp:g}"
            if candidate.adp is not None
            else "real ADP unavailable; rank fallback used only for scoring"
        )
        reasons = [
            f"{value['tier']} value at rank {int(candidate.rank)} ({market_context})",
            roster["impact"],
            draft_plan["impact"],
        ]
        if dynamics["runDetected"]:
            reasons.append(
                f"a {candidate.position} run is active ({dynamics['recentPositionPicks']} of the last {dynamics['window']} picks)"
            )
        if risk["status"] != "unknown":
            reasons.append(f"injury/news status is {risk['status']}")
        elif risk.get("basis") == "recent-news-category":
            categories = sorted(
                {
                    str(item.get("category") or "news")
                    for item in risk.get("recentNews", [])
                    if str(item.get("category") or "").casefold() in RiskNewsAgent._NEWS_SCORES
                }
            )
            reasons.append(
                "recent structured news category adds caution "
                f"({', '.join(categories)}); headline text was not scored"
            )
        else:
            reasons.append("injury/news status is unknown, not assumed healthy")
        reasons.append(f"heuristic chance to return is {opponent['returnProbability']:.0%}")
        return reasons

    @staticmethod
    def _candidate_brief(item: Mapping[str, Any], score: float | None = None) -> dict[str, Any]:
        player = item.get("player") if isinstance(item.get("player"), Mapping) else {}
        value = item.get("specialistDetails")
        value = value.get("value") if isinstance(value, Mapping) else {}
        raw_score = item.get("overallScore") if score is None else score
        result = {
            "name": str(player.get("name") or "Unknown player")[:120],
            "position": _position(player.get("position"))[:16],
            "team": str(player.get("team") or "")[:16],
            "score": round(_number(raw_score, 0.0), 2),
            "rank": round(_number(player.get("rank"), 0.0), 2),
            "adp": (
                round(_number(player.get("adp"), 0.0), 2)
                if player.get("adpAvailable") is True
                else None
            ),
            "adpAvailable": player.get("adpAvailable") is True,
            "tier": str(value.get("tier") or "unknown")[:24],
        }
        player_key = normalize_yahoo_player_key(player.get("playerKey") or player.get("player_key"))
        if player_key:
            result["playerKey"] = player_key
        return result

    @staticmethod
    def _strategy_comparison(
        evaluated: Sequence[Mapping[str, Any]], *, risk_available: bool
    ) -> dict[str, Any]:
        strategies = []
        for strategy in ("conservative", "balanced", "aggressive"):
            scored = []
            for item in evaluated:
                raw_scores = item.get("scores")
                if not isinstance(raw_scores, Mapping):
                    continue
                score, _weights = _strategy_score(
                    raw_scores,
                    strategy,
                    risk_available=risk_available,
                )
                scored.append((score, item))
            scored.sort(
                key=lambda entry: (
                    -entry[0],
                    _number(entry[1].get("player", {}).get("rank"), 10_000),
                    str(entry[1].get("player", {}).get("name") or ""),
                )
            )
            if scored:
                strategies.append(
                    {
                        "strategy": strategy,
                        "primary": LiveDraftRecommendationEngine._candidate_brief(
                            scored[0][1], scored[0][0]
                        ),
                    }
                )
        primary_names = [entry["primary"]["name"] for entry in strategies]
        consensus = bool(primary_names) and len(set(primary_names)) == 1
        return {
            "consensus": consensus,
            "strategies": strategies,
            "summary": (
                f"All strategies prefer {primary_names[0]}."
                if consensus
                else "The primary changes with strategy; compare the trade-offs below."
            )
            if primary_names
            else "Strategy comparison is unavailable until candidates are trustworthy.",
        }

    @staticmethod
    def _position_boards(evaluated: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        boards = []
        for position in _COCKPIT_POSITIONS:
            if position == "OVERALL":
                pool = list(evaluated)
            elif position == "FLEX":
                pool = [
                    item
                    for item in evaluated
                    if _position(item.get("player", {}).get("position")) in {"RB", "WR", "TE"}
                ]
            else:
                pool = [
                    item
                    for item in evaluated
                    if _position(item.get("player", {}).get("position")) == position
                ]
            if not pool:
                continue
            pool.sort(
                key=lambda item: (
                    -_number(item.get("overallScore"), 0.0),
                    _number(item.get("player", {}).get("rank"), 10_000),
                    str(item.get("player", {}).get("name") or ""),
                )
            )
            candidates = [LiveDraftRecommendationEngine._candidate_brief(item) for item in pool[:5]]
            leading_tier = candidates[0]["tier"]
            tier_remaining = sum(
                1
                for item in pool
                if str(item.get("specialistDetails", {}).get("value", {}).get("tier") or "unknown")
                == leading_tier
            )
            next_drop = next(
                (
                    index
                    for index, candidate in enumerate(candidates[1:], start=1)
                    if candidate["tier"] != leading_tier
                ),
                None,
            )
            boards.append(
                {
                    "position": position,
                    "leadingTier": leading_tier,
                    "tierRemaining": min(tier_remaining, 500),
                    "nextTierDropAfter": next_drop,
                    "candidates": candidates,
                }
            )
        return boards

    @staticmethod
    def _position_runs(state: Mapping[str, Any]) -> list[dict[str, Any]]:
        recent = list(state.get("picks", []))[-8:]
        counts = Counter(
            _position(pick.get("position"))
            for pick in recent
            if isinstance(pick, Mapping) and _position(pick.get("position"))
        )
        return [
            {
                "position": position,
                "recentPicks": count,
                "window": len(recent),
                "message": f"{count} {position} selections in the last {len(recent)} picks.",
            }
            for position, count in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
            if count >= 3
        ][:4]

    @staticmethod
    def _roster_plan(
        state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        league: Mapping[str, Any],
        roster_agent: RosterConstructionAgent,
    ) -> dict[str, Any]:
        plan = roster_agent.summary()
        configured = league.get("roster_positions")
        if not isinstance(configured, list):
            configured = league.get("rosterPositions")
        plan["source"] = "configured" if isinstance(configured, list) and configured else "default"
        warnings = []
        open_slots = [
            f"{slot['position']} {slot['open']}" for slot in plan["slots"] if slot["open"] > 0
        ]
        if open_slots:
            warnings.append(f"Open starter slots: {', '.join(open_slots)}.")

        team_count = int(_number(state.get("teamCount"), 0))
        current_pick = int(_number(state.get("currentOverallPick"), 0))
        current_round = (
            ((current_pick - 1) // team_count) + 1 if team_count > 0 and current_pick > 0 else None
        )
        special_teams = [position for position in ("K", "DST") if roster_agent.counts[position] > 0]
        if current_round is not None and current_round <= 10 and special_teams:
            warnings.append(
                f"Early {'/'.join(special_teams)} selection reduced earlier skill-position depth."
            )

        bye_counts: Counter[int] = Counter()
        for pick in state.get("userRoster", []):
            if not isinstance(pick, Mapping):
                continue
            match = next(
                (
                    ranking
                    for ranking in rankings
                    if isinstance(ranking, Mapping) and _same_player(pick, ranking)
                ),
                None,
            )
            if not isinstance(match, Mapping):
                continue
            raw_bye = match.get("bye") or match.get("bye_week")
            if isinstance(raw_bye, int) and not isinstance(raw_bye, bool) and 1 <= raw_bye <= 18:
                bye_counts[raw_bye] += 1
        concentrated = sorted(week for week, count in bye_counts.items() if count >= 3)
        if concentrated:
            warnings.append(
                f"Bye-week concentration: three or more players in week(s) {', '.join(map(str, concentrated))}."
            )
        plan["warnings"] = warnings[:6]
        return plan

    @staticmethod
    def _fallback_tiers(position_boards: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        groups = []
        for board in position_boards:
            if board.get("position") not in {"QB", "RB", "WR", "TE"}:
                continue
            candidates = board.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                continue
            groups.append(
                {
                    "position": board["position"],
                    "tier": str(board.get("leadingTier") or "unknown")[:24],
                    "candidates": [
                        dict(item) for item in candidates[:3] if isinstance(item, Mapping)
                    ],
                }
            )
        return groups[:4]

    @staticmethod
    def _readiness(
        state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        league: Mapping[str, Any],
    ) -> dict[str, Any]:
        configured = league.get("roster_positions")
        if not isinstance(configured, list):
            configured = league.get("rosterPositions")
        checks = [
            {"key": "server", "label": "Local recommendation server connected", "passed": True},
            {
                "key": "ledger",
                "label": "Authoritative numbered ledger complete",
                "passed": state.get("health", {}).get("complete") is True,
            },
            {
                "key": "freshness",
                "label": "Draft state fresh",
                "passed": state.get("health", {}).get("fresh") is True,
            },
            {
                "key": "slot",
                "label": "Your snake-draft slot identified",
                "passed": isinstance(state.get("userDraftSlot"), int),
            },
            {
                "key": "teams",
                "label": "League team count confirmed",
                "passed": state.get("health", {}).get("teamCountSource") == "league",
            },
            {
                "key": "roster",
                "label": "Roster slots configured",
                "passed": isinstance(configured, list) and bool(configured),
            },
            {"key": "rankings", "label": "Ranking pool ready", "passed": bool(rankings)},
        ]
        ready = all(check["passed"] for check in checks)
        return {
            "ready": ready,
            "checks": checks,
            "summary": (
                "Draft cockpit is ready."
                if ready
                else f"{sum(not check['passed'] for check in checks)} readiness check(s) need attention."
            ),
        }

    @staticmethod
    def _recap(
        state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        roster_plan: Mapping[str, Any],
        *,
        blocked: bool,
    ) -> dict[str, Any]:
        recorded = len(state.get("picks", []))
        expected = int(_number(state.get("teamCount"), 0)) * int(
            _number(roster_plan.get("draftableRosterSlots"), 0)
        )
        complete = expected > 0 and recorded >= expected and not blocked
        decisions = []
        for pick in state.get("userRoster", [])[:30]:
            if not isinstance(pick, Mapping) or not isinstance(pick.get("pickNumber"), int):
                continue
            match = next(
                (
                    ranking
                    for ranking in rankings
                    if isinstance(ranking, Mapping) and _same_player(pick, ranking)
                ),
                None,
            )
            if not isinstance(match, Mapping):
                continue
            raw_adp = match.get("average_draft_position")
            if raw_adp is None:
                raw_adp = match.get("adp")
            adp = _optional_positive_number(raw_adp)
            if adp is None:
                continue
            delta = pick["pickNumber"] - adp
            label = "value" if delta >= 8 else ("reach" if delta <= -8 else "near ADP")
            decisions.append(
                {
                    "player": str(pick.get("player") or "Unknown player")[:120],
                    "position": _position(pick.get("position"))[:16],
                    "pickNumber": pick["pickNumber"],
                    "adp": round(adp, 2),
                    "adpDelta": round(delta, 2),
                    "label": label,
                    "basis": "uncalibrated ADP heuristic",
                }
            )
        value_count = sum(item["label"] == "value" for item in decisions)
        reach_count = sum(item["label"] == "reach" for item in decisions)
        status = "blocked" if blocked else ("complete" if complete else "in-progress")
        return {
            "status": status,
            "complete": complete,
            "recordedPicks": recorded,
            "expectedPicks": expected or None,
            "progress": round(min(1.0, recorded / expected), 4) if expected else None,
            "userPickCount": len(state.get("userRoster", [])),
            "valueCount": value_count,
            "reachCount": reach_count,
            "decisions": decisions[:20],
            "summary": (
                "Ledger repair is required before the recap is trustworthy."
                if blocked
                else f"{value_count} value pick(s), {reach_count} reach(es), and {len(decisions) - value_count - reach_count} near-ADP decision(s)."
            ),
        }

    @classmethod
    def _cockpit(
        cls,
        state: Mapping[str, Any],
        rankings: Sequence[Mapping[str, Any]],
        league: Mapping[str, Any],
        roster_agent: RosterConstructionAgent,
        evaluated: Sequence[Mapping[str, Any]],
        *,
        breakout_watch: Mapping[str, Any],
        risk_available: bool,
        blocked: bool,
    ) -> dict[str, Any]:
        roster_plan = cls._roster_plan(state, rankings, league, roster_agent)
        position_boards = [] if blocked else cls._position_boards(evaluated)
        return {
            "strategyComparison": (
                {
                    "consensus": False,
                    "strategies": [],
                    "summary": "Strategy comparison is blocked until the ledger is repaired.",
                }
                if blocked
                else cls._strategy_comparison(evaluated, risk_available=risk_available)
            ),
            "positionBoards": position_boards,
            "positionRuns": [] if blocked else cls._position_runs(state),
            "rosterPlan": roster_plan,
            "fallbackTiers": [] if blocked else cls._fallback_tiers(position_boards),
            "readiness": cls._readiness(state, rankings, league),
            "recap": cls._recap(state, rankings, roster_plan, blocked=blocked),
            "breakoutWatch": dict(breakout_watch),
        }

    @staticmethod
    def _draft_advice(state: Mapping[str, Any]) -> list[str]:
        roster = Counter(
            _position(player.get("position")) for player in state.get("userRoster", [])
        )
        needs = [
            position
            for position, minimum in {"QB": 1, "RB": 2, "WR": 2, "TE": 1}.items()
            if roster[position] < minimum
        ]
        advice = [f"Current roster: {dict(roster)}"]
        if needs:
            advice.append(f"Unfilled core starter positions: {', '.join(needs)}")
        advice.append(
            "Prefer tier value while preserving enough remaining picks to fill required starters"
        )
        return advice

    @staticmethod
    def _next_turn_plan(recommendations: Sequence[Mapping[str, Any]]) -> str:
        if len(recommendations) < 2:
            return "Re-run immediately after new picks sync"
        return f"Re-run after each pick; pivot to {recommendations[1]['player']['name']} if the primary is selected"

    @staticmethod
    def _empty_result(
        base: dict[str, Any], status: str, advice: list[str], started: float
    ) -> dict[str, Any]:
        return {
            **base,
            "status": status,
            "primaryRecommendation": None,
            "alternatives": [],
            "recommendations": [],
            "contingency": {
                "ifPrimaryUnavailable": None,
                "atNextTurn": "Re-run after the state or ranking pool is repaired",
            },
            "critic": {
                "passed": False,
                "checks": {"ledgerComplete": base["state"]["health"]["complete"]},
            },
            "draftAdvice": advice,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 3),
        }
