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
        "draftDynamics": 0.10,
        "opponentModel": 0.12,
        "riskNews": 0.12,
        "scenario": 0.10,
    },
    "aggressive": {
        "value": 0.28,
        "rosterConstruction": 0.15,
        "draftDynamics": 0.12,
        "opponentModel": 0.12,
        "riskNews": 0.08,
        "scenario": 0.25,
    },
    "conservative": {
        "value": 0.30,
        "rosterConstruction": 0.25,
        "draftDynamics": 0.08,
        "opponentModel": 0.10,
        "riskNews": 0.22,
        "scenario": 0.05,
    },
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
            "complete": not missing and not duplicate_numbers and unnumbered_count == 0,
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
    adp: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], fallback_rank: int) -> Candidate | None:
        name = str(value.get("name") or "").strip()
        position = _position(value.get("position"))
        if not name or not position:
            return None
        rank = _number(value.get("rank"), fallback_rank)
        adp = _number(value.get("average_draft_position"), rank)
        return cls(dict(value), name, position, str(value.get("team") or ""), rank, adp)


class PlayerValueAgent:
    name = "playerValue"

    def score(
        self, candidate: Candidate, current_pick: int, pool_size: int
    ) -> tuple[float, dict[str, Any]]:
        rank_quality = max(0.0, 100.0 - ((candidate.rank - 1) / max(1, pool_size)) * 100.0)
        adp_delta = current_pick - candidate.adp
        adp_value = max(0.0, min(100.0, 50.0 + adp_delta * 4.0))
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
            "adpDelta": round(adp_delta, 2),
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
            probability = _inverse_logistic((next_user_pick - candidate.adp) / 6.0)
            basis = "heuristic from ADP and picks until the next user turn"
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
            freshness_value = (
                candidate.raw.get("injury_snapshot_at")
                or candidate.raw.get("retrievedAt")
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

    def _recent_news(
        self, candidate: Candidate, *, fresh: bool
    ) -> list[dict[str, str]]:
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
        injury_fresh, injury_source, injury_updated_at = self._fresh_signal(
            candidate, "injury"
        )
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
                    pressure = _inverse_logistic((candidate.adp - pick_number) / 5.0)
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
        count: int = 5,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        league = dict(league_info or {})
        team_count = int(_number(league.get("teams") or league.get("num_teams"), 0)) or None
        reference_time = now or datetime.now(timezone.utc)
        state = reconcile_live_draft(
            live_context, team_count=team_count, now=reference_time
        )
        warnings = list(state["health"]["warnings"])
        risk_agent = RiskNewsAgent(reference_time)
        ranking_candidates = [
            Candidate.from_mapping(item, index)
            for index, item in enumerate(rankings, start=1)
            if isinstance(item, Mapping)
        ]
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
        base = {
            "source": "live-draft-specialist-suite",
            "strategy": strategy if strategy in _STRATEGY_WEIGHTS else "balanced",
            "generatedAt": live_context.get("generatedAt"),
            "state": state,
            "capabilities": {
                "externalNews": news_available,
                "injuryStatus": injury_available,
                "opponentModel": "heuristic",
                "scenarioSimulation": self.simulations > 0,
                "llmOnRequestPath": False,
            },
            "warnings": warnings,
        }
        if not state["health"]["complete"]:
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

        candidates: list[Candidate] = []
        unresolved_drafted: list[str] = []
        for index, raw in enumerate(rankings, start=1):
            if not isinstance(raw, Mapping):
                continue
            candidate = Candidate.from_mapping(raw, index)
            if candidate is None:
                continue
            if any(_same_player(pick, candidate.raw) for pick in state["picks"]):
                continue
            candidates.append(candidate)
        for pick in state["picks"]:
            if not any(_same_player(pick, raw) for raw in rankings if isinstance(raw, Mapping)):
                unresolved_drafted.append(str(pick.get("player") or "unknown"))
        if unresolved_drafted:
            warnings.append(
                f"{len(unresolved_drafted)} drafted player(s) could not be resolved against rankings"
            )
        if not candidates:
            warnings.append("Every ranking entry was drafted or invalid")
            return self._empty_result(base, "degraded", self._draft_advice(state), started)

        roster_agent = RosterConstructionAgent(state["userRoster"], _roster_positions(league))
        dynamics_agent = DraftDynamicsAgent(state["picks"])
        value_agent = PlayerValueAgent()
        opponent_agent = OpponentModelAgent()
        scenario_agent = ScenarioSimulatorAgent(self.simulations, self.random_seed)
        weights = dict(_STRATEGY_WEIGHTS[base["strategy"]])
        if not risk_available:
            weights["riskNews"] = 0.0
            active_total = sum(weights.values())
            weights = {key: value / active_total for key, value in weights.items()}
        pool_size = max(len(rankings), 100)
        evaluated = []

        for candidate in candidates:
            value, value_detail = value_agent.score(
                candidate, state["currentOverallPick"], pool_size
            )
            roster, roster_detail = roster_agent.score(candidate)
            dynamics, dynamics_detail = dynamics_agent.score(candidate)
            opponent, opponent_detail = opponent_agent.score(candidate, state["nextUserPick"])
            risk, risk_detail = risk_agent.score(candidate)
            scenario, scenario_detail = scenario_agent.score(
                candidate, state["currentOverallPick"], state["nextUserPick"]
            )
            scores = {
                "value": value,
                "rosterConstruction": roster,
                "draftDynamics": dynamics,
                "opponentModel": opponent,
                "riskNews": risk,
                "scenario": scenario,
            }
            effective_weights = dict(weights)
            if risk is None:
                effective_weights["riskNews"] = 0.0
                active_total = sum(effective_weights.values())
                effective_weights = {
                    key: value / active_total for key, value in effective_weights.items()
                }
            overall = sum(
                score * effective_weights[key]
                for key, score in scores.items()
                if score is not None
            )
            reasoning = self._reasoning(
                candidate,
                value_detail,
                roster_detail,
                dynamics_detail,
                risk_detail,
                opponent_detail,
            )
            evaluated.append(
                {
                    "player": {
                        "name": candidate.name,
                        "position": candidate.position,
                        "team": candidate.team,
                        "rank": candidate.rank,
                        "adp": candidate.adp,
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
                        "draftDynamics": dynamics_detail,
                        "opponentModel": opponent_detail,
                        "scenario": scenario_detail,
                    },
                }
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
        result = {
            **base,
            "status": "success" if critic["passed"] else "degraded",
            "primaryRecommendation": selected[0],
            "alternatives": selected[1:3],
            "recommendations": selected,
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
                "draftDynamics": "recent positional-run pressure",
                "opponentModel": "uncalibrated ADP survival heuristic",
                "riskNews": "sourced injury/news when supplied; missing means unknown",
                "scenarioSimulator": {"simulations": self.simulations, "seed": self.random_seed},
                "recommendationCritic": "availability, completeness, and finite-score invariants",
            },
            "critic": critic,
            "draftAdvice": self._draft_advice(state),
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 3),
        }
        return result

    @staticmethod
    def _reasoning(
        candidate: Candidate,
        value: Mapping[str, Any],
        roster: Mapping[str, Any],
        dynamics: Mapping[str, Any],
        risk: Mapping[str, Any],
        opponent: Mapping[str, Any],
    ) -> list[str]:
        reasons = [
            f"{value['tier']} value at rank {int(candidate.rank)} (ADP {candidate.adp:g})",
            roster["impact"],
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
                    if str(item.get("category") or "").casefold()
                    in RiskNewsAgent._NEWS_SCORES
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
