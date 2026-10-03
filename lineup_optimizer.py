from __future__ import annotations

"""Lightweight fallback lineup optimizer.

The original optimizer attempted deep projections and external data pulls but was
fragile against Yahoo payload changes.  This replacement keeps the same public
API so existing callers keep working, while delivering deterministic, best-effort
results based purely on the roster data already returned by the legacy layer.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


BENCH_SLOTS = {
    "BN",
    "BENCH",
    "IR",
    "IR+",
    "IRL",
    "IRR",
    "DNR",
    "NA",
    "N/A",
    "COVID-19",
    "COVID",
}

# Starting slots used when the league's settings can't be read (Yahoo slot names).
DEFAULT_ROSTER_SLOTS = [
    ("QB", 1),
    ("WR", 2),
    ("RB", 2),
    ("TE", 1),
    ("W/R/T", 1),
    ("K", 1),
    ("DEF", 1),
]

# Which base positions each Yahoo slot accepts. Used when a player's own
# eligible_positions are missing, and to order slots from narrowest to widest.
SLOT_MEMBERS = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "W/R": {"WR", "RB"},
    "W/T": {"WR", "TE"},
    "R/T": {"RB", "TE"},
    "W/R/T": {"WR", "RB", "TE"},
    "Q/W/R/T": {"QB", "WR", "RB", "TE"},
    "DL": {"DL", "DE", "DT"},
    "LB": {"LB"},
    "DB": {"DB", "CB", "S"},
    "D": {"DL", "DE", "DT", "LB", "DB", "CB", "S"},
}
BASE_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "DE", "DT", "LB", "DB", "CB", "S"}
SLOT_ALIASES = {"DST": "DEF", "D/ST": "DEF", "FLEX": "W/R/T", "SUPERFLEX": "Q/W/R/T", "OP": "Q/W/R/T"}

# Players with these statuses can't score this week; they start only if nobody else can.
UNAVAILABLE_STATUSES = {"O", "IR", "IR-R", "IR-NR", "PUP", "PUP-R", "PUP-P", "NFI-R", "NFI-A", "SUSP", "NA"}

# Match confidence scores for different Sleeper matching methods
MATCH_CONFIDENCE = {
    "exact": 1.0,
    "normalized": 0.9,
    "variant": 0.8,
    "token_subset": 0.6,
    "fuzzy": 0.4,
    "failed": 0.0,
    "api": 0.9,  # Default for successful API matches
}

# Penalty factors for mismatches
MISMATCH_PENALTY = 0.5


@dataclass
class MatchAnalytics:
    """Analytics for tracking match quality and success rates."""

    total_players: int = 0
    matched_players: int = 0
    exact_matches: int = 0
    normalized_matches: int = 0
    variant_matches: int = 0
    token_subset_matches: int = 0
    fuzzy_matches: int = 0
    failed_matches: int = 0
    position_mismatches: int = 0
    team_mismatches: int = 0
    avg_match_confidence: float = 0.0

    def add_match(self, match_method: str, confidence: float):
        """Record a match attempt."""
        self.total_players += 1

        if not match_method or match_method == "failed":
            self.failed_matches += 1
            return

        self.matched_players += 1

        # Count match types
        base_method = match_method.split("_")[0]
        if base_method == "exact":
            self.exact_matches += 1
        elif base_method == "normalized":
            self.normalized_matches += 1
        elif base_method == "variant":
            self.variant_matches += 1
        elif base_method == "token":
            self.token_subset_matches += 1
        elif base_method == "fuzzy":
            self.fuzzy_matches += 1

        # Count mismatches
        if "_pos_mismatch" in match_method:
            self.position_mismatches += 1
        if "_team_mismatch" in match_method:
            self.team_mismatches += 1

        # Update average confidence
        if self.matched_players > 0:
            total_confidence = self.avg_match_confidence * (self.matched_players - 1) + confidence
            self.avg_match_confidence = total_confidence / self.matched_players

    def get_success_rate(self) -> float:
        """Get overall match success rate."""
        return self.matched_players / self.total_players if self.total_players > 0 else 0.0

    def get_quality_distribution(self) -> Dict[str, float]:
        """Get distribution of match quality."""
        if self.matched_players == 0:
            return {}

        return {
            "exact": self.exact_matches / self.matched_players,
            "normalized": self.normalized_matches / self.matched_players,
            "variant": self.variant_matches / self.matched_players,
            "token_subset": self.token_subset_matches / self.matched_players,
            "fuzzy": self.fuzzy_matches / self.matched_players,
        }


def _coerce_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str) and not value.strip():
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_position(raw: Any) -> str:
    if not raw:
        return "BN"
    if isinstance(raw, dict):
        if "position" in raw:
            return str(raw.get("position", "BN")).upper()
        for val in raw.values():
            if isinstance(val, dict) and "position" in val:
                return str(val.get("position", "BN")).upper()
    return str(raw).upper()


def _canonical_slot(name: Any) -> str:
    slot = str(name or "").strip().upper()
    return SLOT_ALIASES.get(slot, slot)


def _find_roster_positions(payload: Any) -> Optional[List[Any]]:
    """Locate the roster_positions list anywhere in a Yahoo settings payload."""
    if isinstance(payload, dict):
        if "roster_positions" in payload:
            found = payload["roster_positions"]
            if isinstance(found, dict):  # {"0": {...}, "1": {...}, "count": n}
                found = [v for k, v in found.items() if k != "count"]
            return found if isinstance(found, list) else None
        children: Iterable[Any] = payload.values()
    elif isinstance(payload, list):
        children = payload
    else:
        return None
    for child in children:
        found = _find_roster_positions(child)
        if found is not None:
            return found
    return None


def parse_roster_slots(settings: Any) -> List[tuple[str, int]]:
    """Starting slots as (Yahoo slot name, count), from a league settings payload.

    Accepts the raw ``league/{key}/settings`` response or an already-extracted list
    of ``{"position": ..., "count": ...}`` dicts. Bench and IR slots are dropped.
    Returns an empty list when nothing usable is found.
    """
    entries = settings if isinstance(settings, list) else _find_roster_positions(settings) or []
    slots: List[tuple[str, int]] = []
    for entry in entries:
        if isinstance(entry, dict) and "roster_position" in entry:
            entry = entry["roster_position"]
        if not isinstance(entry, dict):
            continue
        slot = _canonical_slot(entry.get("position"))
        if not slot or slot in BENCH_SLOTS or slot.startswith("IR") or slot.startswith("IL"):
            continue
        if str(entry.get("is_starting_position", 1)) in ("0", "False", "false"):
            continue
        count = _coerce_int(entry.get("count"), default=1)
        if count > 0:
            slots.append((slot, count))
    return slots


def _base_positions(player: "Player") -> set[str]:
    """The player's real positions (e.g. {"WR"}), never the slot he sits in."""
    raw = player.raw.get("display_position") or player.raw.get("primary_position")
    if not raw and _canonical_slot(player.position) in BASE_POSITIONS:
        raw = player.position  # a single-position slot is also his position
    return {_canonical_slot(p) for p in str(raw or "").split(",") if p.strip()}


def _eligible_slots(player: "Player") -> set[str]:
    """Every Yahoo slot this player may fill, from Yahoo's list plus his base positions."""
    eligible = {_canonical_slot(p) for p in player.raw.get("eligible_positions") or []}
    base = _base_positions(player)
    eligible |= base
    eligible |= {slot for slot, members in SLOT_MEMBERS.items() if base & members}
    return {slot for slot in eligible if slot not in BENCH_SLOTS and not slot.startswith("IR")}


def _is_available(player: "Player", week: Optional[int]) -> bool:
    if player.on_bye or (isinstance(week, int) and isinstance(player.bye, int) and player.bye == week):
        return False
    return str(player.status or "").upper() not in UNAVAILABLE_STATUSES


def _strategy_score(player: "Player", strategy: str) -> float:
    median = player.composite_score or max(player.yahoo_projection, player.sleeper_projection)
    if strategy in ("floor", "conservative"):
        return player.floor_projection or median
    if strategy in ("ceiling", "aggressive"):
        return player.ceiling_projection or median
    return median


def _calculate_match_confidence(match_method: str) -> float:
    """Calculate confidence score for Sleeper match quality."""
    if not match_method:
        return 0.0

    # Extract base method and check for mismatches
    base_method = match_method.split("_")[0]
    has_mismatch = "_mismatch" in match_method

    # Get base confidence
    confidence = MATCH_CONFIDENCE.get(base_method, 0.0)

    # Apply mismatch penalty
    if has_mismatch:
        confidence *= MISMATCH_PENALTY

    return confidence


def _calculate_dynamic_weights(
    yahoo_proj: float, sleeper_proj: float, match_confidence: float
) -> Dict[str, float]:
    """Calculate dynamic projection weights based on data quality and match confidence.

    The weights blend the projections that are actually present, so they sum to 1.0
    over those sources: two identical projections blend to that same value, and a
    player with only one projection gets that projection rather than a fraction of it.
    """

    # Even split when the Sleeper match is certain; a shakier match shifts weight to Yahoo.
    sleeper_weight = 0.5 * max(0.0, min(1.0, match_confidence))
    yahoo_weight = 1.0 - sleeper_weight

    # A source with no projection contributes nothing, so its share goes to the other one.
    has_yahoo = bool(yahoo_proj)
    has_sleeper = bool(sleeper_proj) and sleeper_weight > 0
    if has_yahoo and not has_sleeper:
        yahoo_weight, sleeper_weight = 1.0, 0.0
    elif has_sleeper and not has_yahoo:
        yahoo_weight, sleeper_weight = 0.0, 1.0

    return {
        "yahoo": yahoo_weight,
        "sleeper": sleeper_weight,
        "match_confidence": match_confidence,
    }


@dataclass
class Player:
    """Simple player model that mirrors the attributes used by our callers."""

    name: str
    position: str
    team: str
    opponent: str = ""
    status: str = "OK"
    yahoo_projection: float = 0.0
    sleeper_projection: float = 0.0
    sleeper_projection_std: float = 0.0
    sleeper_projection_ppr: float = 0.0
    sleeper_projection_half_ppr: float = 0.0
    sleeper_id: str = ""
    sleeper_status: str = ""
    sleeper_injury_status: str = ""
    sleeper_match_method: str = ""
    player_tier: str = "starter"
    matchup_score: int = 50
    matchup_description: str = "No matchup context"
    trending_score: int = 0
    injury_status: str = "Healthy"
    injury_probability: float = 0.0
    ownership_pct: float = 0.0
    recent_performance: List[float] = field(default_factory=list)
    season_avg: float = 0.0
    target_share: float = 0.0
    snap_count_pct: float = 0.0
    weather_impact: str = "Unknown"
    vegas_total: float = 0.0
    team_implied_total: float = 0.0
    spread: float = 0.0
    defense_rank_allowed: str = "Unknown"
    value: float = 0.0
    value_score: float = 0.0
    floor_projection: float = 0.0
    ceiling_projection: float = 0.0
    consistency_score: float = 0.0
    risk_level: str = "medium"
    composite_score: float = 0.0
    # New expert advice fields
    expert_tier: str = ""
    expert_recommendation: str = ""
    expert_confidence: int = 0
    expert_advice: str = ""
    search_rank: int = 500
    # Match analytics
    match_confidence: float = 0.0
    match_analytics: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    # Enhancement layer fields (bye weeks, recent stats, performance flags)
    bye: Any = "N/A"  # Bye week number (from Yahoo API)
    on_bye: bool = False  # Is player on bye this week?
    recent_performance_data: Any = None  # RecentPerformance object from enhancement
    performance_flags: List[str] = field(default_factory=list)  # ["BREAKOUT", "TRENDING_UP", etc]
    enhancement_context: str = ""  # Human-readable context message
    adjusted_projection: float = 0.0  # Projection adjusted based on recent performance

    def is_valid(self) -> bool:
        return bool(self.name and self.team)


class LineupOptimizer:
    """Best-effort lineup helper that works entirely offline."""

    def __init__(self) -> None:
        pass

    async def parse_yahoo_roster(self, roster_payload: Dict[str, Any]) -> List[Player]:
        """Convert a roster payload into Player objects.

        Supports both the simplified JSON returned by ``ff_get_roster`` and the
        raw Yahoo payload by delegating to ``parse_team_roster`` when needed.
        """

        entries: List[Dict[str, Any]] = []
        if isinstance(roster_payload, dict):
            roster_obj = roster_payload.get("roster")
            if isinstance(roster_obj, list):
                entries = roster_obj
            else:
                # Fallback: try using the legacy parser for raw Yahoo data
                try:
                    from fantasy_football_multi_league import parse_team_roster  # type: ignore

                    entries = parse_team_roster(roster_payload)
                except Exception:
                    entries = []
        players: List[Player] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or entry.get("full_name") or "").strip()
            if not name:
                continue
            team = str(
                entry.get("team")
                or entry.get("editorial_team_abbr")
                or entry.get("team_abbr")
                or entry.get("team_abbreviation")
                or ""
            ).strip()
            position = _normalize_position(
                entry.get("position")
                or entry.get("selected_position")
                or entry.get("display_position")
                or "BN"
            )
            player = Player(
                name=name,
                position=position,
                team=team,
                opponent=str(entry.get("opponent") or entry.get("opponent_abbr") or ""),
                status=str(entry.get("status") or "OK"),
                yahoo_projection=_coerce_float(
                    entry.get("yahoo_projection") or entry.get("projection")
                ),
                sleeper_projection=_coerce_float(entry.get("sleeper_projection")),
                sleeper_projection_std=_coerce_float(entry.get("sleeper_projection_std")),
                sleeper_projection_ppr=_coerce_float(entry.get("sleeper_projection_ppr")),
                sleeper_projection_half_ppr=_coerce_float(entry.get("sleeper_projection_half_ppr")),
                sleeper_id=str(entry.get("sleeper_id") or ""),
                sleeper_status=str(entry.get("sleeper_status") or ""),
                sleeper_injury_status=str(entry.get("sleeper_injury_status") or ""),
                sleeper_match_method=str(entry.get("sleeper_match_method") or ""),
                player_tier=str(entry.get("player_tier") or "starter"),
                matchup_score=_coerce_int(entry.get("matchup_score"), default=50),
                matchup_description=str(entry.get("matchup_description") or "No matchup context"),
                trending_score=_coerce_int(entry.get("trending_score"), default=0),
                injury_status=str(entry.get("injury_status") or "Healthy"),
                injury_probability=_coerce_float(entry.get("injury_probability")),
                ownership_pct=_coerce_float(entry.get("ownership_pct")),
                season_avg=_coerce_float(entry.get("season_avg")),
                target_share=_coerce_float(entry.get("target_share")),
                snap_count_pct=_coerce_float(entry.get("snap_count_pct")),
                weather_impact=str(entry.get("weather_impact") or "Unknown"),
                vegas_total=_coerce_float(entry.get("vegas_total")),
                team_implied_total=_coerce_float(entry.get("team_implied_total")),
                spread=_coerce_float(entry.get("spread")),
                defense_rank_allowed=str(entry.get("def_rank_vs_pos") or "Unknown"),
                value=_coerce_float(entry.get("value") or entry.get("value_score")),
                value_score=_coerce_float(entry.get("value_score")),
                floor_projection=_coerce_float(entry.get("floor_projection")),
                ceiling_projection=_coerce_float(entry.get("ceiling_projection")),
                consistency_score=_coerce_float(entry.get("consistency_score")),
                risk_level=str(entry.get("risk_level") or "medium"),
                composite_score=_coerce_float(entry.get("composite_score")),
                bye=entry.get("bye", "N/A"),  # Extract bye week from Yahoo data
                raw=entry,
            )
            players.append(player)
        return players

    async def enhance_with_external_data(
        self,
        players: Sequence[Player],
        *,
        week: Optional[int] = None,
    ) -> List[Player]:
        """Enhance players with Sleeper data including rankings, advice, and matchup analysis."""

        enhanced: List[Player] = []
        match_analytics = MatchAnalytics()

        try:
            from sleeper_api import sleeper_client

            # Get current season and week once for all players
            from sleeper_api import get_current_season, get_current_week

            current_season = await get_current_season()
            current_week = await get_current_week()

            for player in players:
                # Create a copy to avoid modifying the original
                enhanced_player = Player(
                    name=player.name,
                    position=player.position,
                    team=player.team,
                    opponent=player.opponent,
                    status=player.status,
                    yahoo_projection=player.yahoo_projection,
                    sleeper_projection=player.sleeper_projection,
                    sleeper_projection_std=player.sleeper_projection_std,
                    sleeper_projection_ppr=player.sleeper_projection_ppr,
                    sleeper_projection_half_ppr=player.sleeper_projection_half_ppr,
                    sleeper_id=player.sleeper_id,
                    sleeper_status=player.sleeper_status,
                    sleeper_injury_status=player.sleeper_injury_status,
                    sleeper_match_method=player.sleeper_match_method,
                    player_tier=player.player_tier,
                    matchup_score=player.matchup_score,
                    matchup_description=player.matchup_description,
                    trending_score=player.trending_score,
                    injury_status=player.injury_status,
                    injury_probability=player.injury_probability,
                    ownership_pct=player.ownership_pct,
                    recent_performance=player.recent_performance.copy(),
                    season_avg=player.season_avg,
                    target_share=player.target_share,
                    snap_count_pct=player.snap_count_pct,
                    weather_impact=player.weather_impact,
                    vegas_total=player.vegas_total,
                    team_implied_total=player.team_implied_total,
                    spread=player.spread,
                    defense_rank_allowed=player.defense_rank_allowed,
                    value=player.value,
                    value_score=player.value_score,
                    floor_projection=player.floor_projection,
                    ceiling_projection=player.ceiling_projection,
                    consistency_score=player.consistency_score,
                    risk_level=player.risk_level,
                    composite_score=player.composite_score,
                    raw=player.raw.copy(),
                    bye=player.bye,
                    on_bye=player.on_bye,
                    recent_performance_data=player.recent_performance_data,
                    performance_flags=player.performance_flags.copy(),
                    enhancement_context=player.enhancement_context,
                    adjusted_projection=player.adjusted_projection,
                )

                use_week = week or current_week
                logger.debug(
                    "enhance_with_external_data: player=%s original_bye=%r requested_week=%s api_current_week=%s use_week=%s",
                    player.name,
                    player.bye,
                    week,
                    current_week,
                    use_week,
                )

                try:
                    # Get Sleeper player mapping and basic data
                    sleeper_id = await sleeper_client.map_yahoo_to_sleeper(
                        player.name, position=player.position, team=player.team
                    )

                    if sleeper_id:
                        enhanced_player.sleeper_id = sleeper_id
                        enhanced_player.sleeper_match_method = "api"
                        enhanced_player.bye = player.bye

                        # Fetch projections for this player
                        try:
                            projections = await sleeper_client.get_projections(
                                current_season, use_week
                            )
                            if sleeper_id in projections:
                                proj_data = projections[sleeper_id]
                                # Sleeper projections typically have 'projected_stats' with 'pts' or position-specific
                                stats = proj_data.get("projected_stats", {})
                                if isinstance(stats, list):
                                    # Sum pts from list of stats if present
                                    enhanced_player.sleeper_projection = sum(
                                        _coerce_float(s.get("pts", 0)) for s in stats
                                    )
                                    enhanced_player.sleeper_projection_std = sum(
                                        _coerce_float(s.get("pts_std", 0)) for s in stats
                                    )
                                    enhanced_player.sleeper_projection_ppr = sum(
                                        _coerce_float(s.get("pts_ppr", 0)) for s in stats
                                    )
                                    enhanced_player.sleeper_projection_half_ppr = sum(
                                        _coerce_float(s.get("pts_half_ppr", 0)) for s in stats
                                    )
                                else:
                                    # Dict or direct pts
                                    enhanced_player.sleeper_projection = _coerce_float(
                                        stats.get("pts") or proj_data.get("pts", 0)
                                    )
                                    enhanced_player.sleeper_projection_std = _coerce_float(
                                        stats.get("pts_std") or proj_data.get("pts_std", 0)
                                    )
                                    enhanced_player.sleeper_projection_ppr = _coerce_float(
                                        stats.get("pts_ppr")
                                        or proj_data.get(
                                            "pts_ppr", enhanced_player.sleeper_projection
                                        )
                                    )
                                    enhanced_player.sleeper_projection_half_ppr = _coerce_float(
                                        stats.get("pts_half_ppr")
                                        or proj_data.get(
                                            "pts_half_ppr", enhanced_player.sleeper_projection
                                        )
                                    )
                        except Exception:
                            enhanced_player.sleeper_projection = 0.0  # Fallback if projections fail

                        # Get expert advice for this player
                        advice = await sleeper_client.get_expert_advice(player.name, week=use_week)
                        if advice and advice.get("confidence", 0) > 0:
                            enhanced_player.expert_tier = advice.get("tier", "starter")
                            enhanced_player.expert_recommendation = advice.get(
                                "recommendation", "Start"
                            )
                            enhanced_player.expert_confidence = advice.get("confidence", 50)
                            enhanced_player.expert_advice = advice.get(
                                "advice", "No advice available"
                            )
                            enhanced_player.search_rank = advice.get("search_rank", 500)
                            enhanced_player.matchup_description = advice.get(
                                "advice", "No advice available"
                            )

                            # Convert confidence to matchup score (0-100 -> 0-100)
                            enhanced_player.matchup_score = advice.get("confidence", 50)

                            # Set risk level based on tier and confidence
                            confidence = advice.get("confidence", 50)
                            if confidence >= 70:
                                enhanced_player.risk_level = "low"
                            elif confidence >= 50:
                                enhanced_player.risk_level = "medium"
                            else:
                                enhanced_player.risk_level = "high"

                        # Try to get trending data
                        try:
                            trending_adds = await sleeper_client.get_trending_players(
                                "nfl", "add", hours=24
                            )
                            trending_drops = await sleeper_client.get_trending_players(
                                "nfl", "drop", hours=24
                            )

                            # Check if this player is trending
                            trending_add_ids = [p.get("player_id") for p in trending_adds]
                            trending_drop_ids = [p.get("player_id") for p in trending_drops]

                            if sleeper_id in trending_add_ids:
                                enhanced_player.trending_score = 75  # Trending up
                            elif sleeper_id in trending_drop_ids:
                                enhanced_player.trending_score = 25  # Trending down
                            else:
                                enhanced_player.trending_score = 50  # Neutral
                        except Exception:
                            enhanced_player.trending_score = 50  # Default if trending fails

                    # ===== ENHANCEMENT LAYER: Bye weeks & recent stats context =====
                    try:
                        from src.services.player_enhancement import enhance_player_with_context

                        enhancement = await enhance_player_with_context(
                            enhanced_player,
                            current_week=use_week,
                            season=current_season,
                            sleeper_api=sleeper_client,
                        )

                        # Apply bye week override
                        if enhancement.on_bye:
                            enhanced_player.sleeper_projection = 0.0
                            enhanced_player.yahoo_projection = 0.0
                            enhanced_player.sleeper_projection_ppr = 0.0
                            enhanced_player.sleeper_projection_std = 0.0
                            enhanced_player.sleeper_projection_half_ppr = 0.0
                            enhanced_player.expert_recommendation = (
                                enhancement.recommendation_override
                            )
                            enhanced_player.risk_level = "n/a"
                            enhanced_player.player_tier = "bye"

                        # Store enhancement data on player
                        enhanced_player.on_bye = enhancement.on_bye
                        enhanced_player.recent_performance_data = enhancement.recent_performance
                        enhanced_player.performance_flags = enhancement.performance_flags
                        enhanced_player.enhancement_context = enhancement.context_message

                        # Use adjusted projection if available
                        if enhancement.adjusted_projection is not None and not enhancement.on_bye:
                            enhanced_player.adjusted_projection = enhancement.adjusted_projection
                        else:
                            enhanced_player.adjusted_projection = enhanced_player.sleeper_projection

                    except Exception:
                        # If enhancement fails, continue with original data
                        logger.exception("Player enhancement failed for %s", player.name)
                        enhanced_player.on_bye = False
                        enhanced_player.recent_performance_data = None
                        enhanced_player.performance_flags = []
                        enhanced_player.enhancement_context = "Enhancement unavailable"
                        enhanced_player.adjusted_projection = enhanced_player.sleeper_projection

                except Exception:
                    # If Sleeper lookup fails, keep original data
                    enhanced_player.sleeper_match_method = "failed"

                # Populate derived metrics with dynamic weighting
                match_confidence = _calculate_match_confidence(enhanced_player.sleeper_match_method)
                weights = _calculate_dynamic_weights(
                    enhanced_player.yahoo_projection,
                    enhanced_player.sleeper_projection,
                    match_confidence,
                )

                # Track match analytics
                match_analytics.add_match(enhanced_player.sleeper_match_method, match_confidence)

                # Populate player match analytics fields
                enhanced_player.match_confidence = match_confidence
                enhanced_player.match_analytics = {
                    "method": enhanced_player.sleeper_match_method,
                    "confidence": match_confidence,
                    "has_mismatch": "_mismatch" in (enhanced_player.sleeper_match_method or ""),
                    "weights_used": weights,
                }

                # Calculate weighted composite score
                if enhanced_player.composite_score == 0.0:
                    yahoo_component = enhanced_player.yahoo_projection * weights["yahoo"]
                    sleeper_component = enhanced_player.sleeper_projection * weights["sleeper"]
                    enhanced_player.composite_score = yahoo_component + sleeper_component

                    # Add debugging info to raw data for analysis
                    enhanced_player.raw["weighting_info"] = {
                        "match_confidence": match_confidence,
                        "yahoo_weight": weights["yahoo"],
                        "sleeper_weight": weights["sleeper"],
                        "yahoo_component": yahoo_component,
                        "sleeper_component": sleeper_component,
                    }
                if enhanced_player.floor_projection == 0.0:
                    enhanced_player.floor_projection = max(
                        enhanced_player.composite_score * 0.75, 0.0
                    )
                if enhanced_player.ceiling_projection == 0.0:
                    enhanced_player.ceiling_projection = max(
                        enhanced_player.composite_score * 1.25, enhanced_player.floor_projection
                    )
                if enhanced_player.matchup_description == "No matchup context":
                    enhanced_player.matchup_description = f"Week {use_week} outlook"
                if not enhanced_player.matchup_score:
                    enhanced_player.matchup_score = 50

                enhanced.append(enhanced_player)

            # Add analytics summary to the first player's raw data for debugging
            if enhanced:
                enhanced[0].raw["session_analytics"] = {
                    "total_players": match_analytics.total_players,
                    "success_rate": match_analytics.get_success_rate(),
                    "avg_confidence": match_analytics.avg_match_confidence,
                    "quality_distribution": match_analytics.get_quality_distribution(),
                    "mismatches": {
                        "position": match_analytics.position_mismatches,
                        "team": match_analytics.team_mismatches,
                    },
                }

        except ImportError:
            # If Sleeper API not available, fall back to basic enhancement
            for player in players:
                if player.composite_score == 0.0:
                    # Use simple fallback without dynamic weighting
                    match_confidence = _calculate_match_confidence(player.sleeper_match_method)
                    if match_confidence > 0.5 and player.sleeper_projection:
                        # Trust existing Sleeper data if match confidence is good
                        weights = _calculate_dynamic_weights(
                            player.yahoo_projection, player.sleeper_projection, match_confidence
                        )
                        yahoo_component = player.yahoo_projection * weights["yahoo"]
                        sleeper_component = player.sleeper_projection * weights["sleeper"]
                        player.composite_score = yahoo_component + sleeper_component
                    else:
                        # Fall back to Yahoo only or simple average
                        player.composite_score = (
                            player.yahoo_projection or player.sleeper_projection or 0.0
                        )
                if player.floor_projection == 0.0:
                    player.floor_projection = max(player.composite_score * 0.75, 0.0)
                if player.ceiling_projection == 0.0:
                    player.ceiling_projection = max(
                        player.composite_score * 1.25, player.floor_projection
                    )
                if player.matchup_description == "No matchup context":
                    player.matchup_description = f"Week {week or 'current'} outlook"
                if not player.matchup_score:
                    player.matchup_score = 50
                enhanced.append(player)

        return enhanced

    async def optimize_lineup_smart(
        self,
        players: Sequence[Player],
        strategy: str = "balanced",
        week: Optional[int] = None,
        use_llm: bool = False,
        roster_slots: Optional[Sequence[tuple[str, int]]] = None,
    ) -> Dict[str, Any]:
        """Fill the league's starting slots from the whole roster, bench included.

        ``roster_slots`` is the league's starting configuration as (Yahoo slot, count)
        pairs, e.g. from ``parse_roster_slots``; without it a standard
        QB/2RB/2WR/TE/W-R-T/K/DEF lineup is assumed. Slots are filled narrowest
        first (WR before W/R before W/R/T), each with the best remaining eligible
        player by the strategy's score. Injured, suspended, and bye-week players
        start only when no one else can fill the slot.
        """

        slots = list(roster_slots) if roster_slots else list(DEFAULT_ROSTER_SLOTS)
        errors: List[str] = []
        if not roster_slots:
            errors.append("League roster settings unavailable; assumed a standard lineup.")

        def breadth(slot: str) -> int:
            return len(SLOT_MEMBERS.get(slot, {slot}))

        eligible = {id(p): _eligible_slots(p) for p in players}
        ranked = sorted(
            players,
            key=lambda p: (_is_available(p, week), _strategy_score(p, strategy)),
            reverse=True,
        )

        starters: Dict[str, Player] = {}
        used: set[int] = set()
        # Stable sort keeps the league's own slot order among slots of equal breadth.
        for slot, count in sorted(slots, key=lambda s: breadth(s[0])):
            for n in range(count):
                key = f"{slot}{n + 1}" if count > 1 else slot
                pick = next(
                    (p for p in ranked if id(p) not in used and slot in eligible[id(p)]), None
                )
                if pick is None:
                    errors.append(f"No player on the roster can fill {key}.")
                    continue
                if not _is_available(pick, week):
                    status = str(pick.status or "").upper()
                    reason = f"listed {status}" if status in UNAVAILABLE_STATUSES else "on bye"
                    errors.append(f"{pick.name} starts at {key} but is {reason}.")
                starters[key] = pick
                used.add(id(pick))

        # Present starters in the league's slot order rather than fill order.
        order = {slot: i for i, (slot, _) in enumerate(slots)}
        starters = dict(
            sorted(starters.items(), key=lambda kv: order.get(kv[0].rstrip("0123456789"), 99))
        )
        bench = [p for p in ranked if id(p) not in used]

        recommendations = [f"Start {p.name} at {pos}" for pos, p in starters.items()]
        data_quality = {
            "total_players": len(players),
            "valid_players": sum(1 for p in players if p.is_valid()),
            "players_with_projections": sum(
                1 for p in players if (p.yahoo_projection or p.sleeper_projection)
            ),
            "players_with_matchup_data": sum(
                1
                for p in players
                if p.matchup_description and p.matchup_description != "No matchup context"
            ),
            "players_with_sleeper_match": sum(
                1 for p in players if p.sleeper_id and p.sleeper_match_method not in ["failed", ""]
            ),
            "avg_match_confidence": (
                sum(p.match_confidence for p in players) / len(players) if players else 0
            ),
            "high_confidence_matches": sum(1 for p in players if p.match_confidence >= 0.8),
            "low_confidence_matches": sum(1 for p in players if 0 < p.match_confidence < 0.6),
        }

        return {
            "status": "success",
            "strategy_used": strategy,
            "week": week or "current",
            "starters": starters,
            "bench": bench,
            "recommendations": recommendations,
            "errors": errors,
            "data_quality": data_quality,
        }


lineup_optimizer = LineupOptimizer()

__all__ = ["Player", "LineupOptimizer", "lineup_optimizer"]
