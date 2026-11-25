"""Player MCP tool handlers."""

from typing import Any, Dict

from src.parsers.yahoo_parsers import parse_percent_owned

# These will be injected from main file
yahoo_api_call = None
get_waiver_wire_players = None


async def handle_ff_get_players(arguments: dict) -> dict:
    """Get top available players with optional enhanced data.

    Args:
        arguments: Dict containing:
            - league_key: League identifier (required)
            - position: Filter by position (optional)
            - count: Number of players to return (default: 10)
            - week: Specific week (optional)
            - include_analysis: Include analysis (default: False)
            - include_projections: Include projections (default: True)
            - include_external_data: Include Sleeper data (default: True)

    Returns:
        Dict with player data and optional enhancements
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key = arguments.get("league_key")
    position = arguments.get("position", "")
    count = arguments.get("count", 10)
    week = arguments.get("week")
    include_analysis = arguments.get("include_analysis", False)
    include_projections = arguments.get("include_projections", True)
    include_external_data = arguments.get("include_external_data", True)

    pos_filter = f";position={position}" if position else ""
    # out=percent_owned: Yahoo only reports ownership when asked for it.
    data = await yahoo_api_call(
        f"league/{league_key}/players;status=A{pos_filter};count={count};out=percent_owned"
    )

    def _iter_payload_dicts(container: Any):
        if isinstance(container, dict):
            yield container
        elif isinstance(container, list):
            for item in container:
                yield from _iter_payload_dicts(item)

    basic_players: list[dict[str, Any]] = []
    league = data.get("fantasy_content", {}).get("league", [])
    for item in league:
        if not (isinstance(item, dict) and "players" in item):
            continue
        players_data = item["players"]
        if not isinstance(players_data, dict):
            continue

        for key, player_entry in players_data.items():
            if key == "count" or not isinstance(player_entry, dict):
                continue
            player_array = player_entry.get("player")
            if not isinstance(player_array, list):
                continue

            player_info: dict[str, Any] = {}
            for payload in _iter_payload_dicts(player_array):
                if "name" in payload and isinstance(payload["name"], dict):
                    player_info["name"] = payload["name"].get("full")
                if "editorial_team_abbr" in payload:
                    player_info["team"] = payload["editorial_team_abbr"]
                if "display_position" in payload:
                    player_info["position"] = payload["display_position"]
                if "percent_owned" in payload:
                    owned, change = parse_percent_owned(payload["percent_owned"])
                    # Yahoo leaves the value out when nobody owns the player.
                    player_info["owned_pct"] = owned if owned is not None else 0.0
                    if change is not None:
                        player_info["weekly_change"] = change
                # Add injury, bye as in waiver
                if "status" in payload:
                    player_info["injury_status"] = payload["status"]
                if "bye_weeks" in payload:
                    player_info["bye"] = payload["bye_weeks"].get("week", "N/A")
            if player_info:
                basic_players.append(player_info)

    result = {
        "status": "success",
        "league_key": league_key,
        "position": position or "all",
        "total_players": len(basic_players),
        "players": basic_players[:count],
    }

    needs_enhancement = include_projections or include_external_data or include_analysis

    if not needs_enhancement:
        return result

    try:
        from lineup_optimizer import lineup_optimizer, Player
    except ImportError as exc:
        result["note"] = f"Enhanced data unavailable: {exc}"
        return result

    try:
        # Parse and enhance
        optimizer_payload = {
            "league_key": league_key,
            "roster": basic_players,  # Treat as roster for parsing
        }
        enhanced_players = await lineup_optimizer.parse_yahoo_roster(optimizer_payload)
        if enhanced_players:
            enhanced_players = await lineup_optimizer.enhance_with_external_data(
                enhanced_players, week=week
            )

            def serialize_free_agent_player(player: Player) -> Dict[str, Any]:
                base = {
                    "name": player.name,
                    "position": player.position,
                    "team": player.team,
                    "opponent": player.opponent or "N/A",
                    "status": "Available",
                    "yahoo_projection": player.yahoo_projection if include_projections else None,
                    "sleeper_projection": (
                        player.sleeper_projection if include_projections else None
                    ),
                    "sleeper_id": player.sleeper_id,
                    "sleeper_match_method": player.sleeper_match_method,
                    "floor_projection": player.floor_projection if include_projections else None,
                    "ceiling_projection": (
                        player.ceiling_projection if include_projections else None
                    ),
                    "consistency_score": player.consistency_score,
                    "player_tier": player.player_tier,
                    "matchup_score": player.matchup_score if include_external_data else None,
                    "matchup_description": (
                        player.matchup_description if include_external_data else None
                    ),
                    "trending_score": player.trending_score if include_external_data else None,
                    "risk_level": player.risk_level,
                    "owned_pct": next(
                        (
                            p.get("owned_pct") or 0
                            for p in basic_players
                            if p.get("name", "").lower() == player.name.lower()
                        ),
                        0,
                    ),
                    "injury_status": getattr(player, "injury_status", "Healthy"),
                    "bye": next(
                        (
                            p.get("bye")
                            for p in basic_players
                            if p.get("name", "").lower() == player.name.lower()
                        ),
                        "N/A",
                    ),
                    # Enhancement layer fields
                    "bye_week": player.bye if include_external_data else None,
                    "on_bye": player.on_bye if include_external_data else False,
                    "performance_flags": player.performance_flags if include_external_data else [],
                    "enhancement_context": (
                        player.enhancement_context if include_external_data else None
                    ),
                    "adjusted_projection": (
                        player.adjusted_projection if include_external_data else None
                    ),
                }

                # Add analysis if flagged
                if include_analysis:
                    proj = (player.yahoo_projection or 0) + (player.sleeper_projection or 0)
                    owned = base.get("owned_pct", 0.0)

                    # Adjust analysis for bye weeks
                    if player.on_bye:
                        base["free_agent_value"] = 0.0
                        base["analysis"] = f"ON BYE - Do not add this week"
                    else:
                        base["free_agent_value"] = round(proj * (1 - owned / 100), 1)
                        analysis_parts = [f"Low ownership ({owned}%), proj ({proj:.1f})"]

                        # Add recent performance context
                        if player.recent_performance_data:
                            recent = player.recent_performance_data
                            analysis_parts.append(
                                f"L{recent.weeks_analyzed}W avg: {recent.avg_points:.1f}"
                            )

                        # Add performance flags
                        if player.performance_flags:
                            analysis_parts.append(", ".join(player.performance_flags))

                        base["analysis"] = " | ".join(analysis_parts)

                return base

            enhanced_list = [
                serialize_free_agent_player(p) for p in enhanced_players if p.is_valid()
            ]
            if include_analysis:
                enhanced_list.sort(key=lambda x: x.get("free_agent_value", 0), reverse=True)
            elif include_projections:
                enhanced_list.sort(
                    key=lambda x: (x.get("sleeper_projection") or 0)
                    + (x.get("yahoo_projection") or 0),
                    reverse=True,
                )

            result.update(
                {
                    "enhanced_players": enhanced_list,
                    "analysis_context": {
                        "data_sources": ["Yahoo"] + (["Sleeper"] if include_external_data else []),
                        "includes": {
                            "projections": include_projections,
                            "external_data": include_external_data,
                            "analysis": include_analysis,
                        },
                        "week": week or "current",
                    },
                }
            )
        else:
            result["note"] = "No players could be enhanced"
    except Exception as exc:
        result["note"] = f"Enhancement failed: {exc}. Using basic data."

    return result


async def handle_ff_compare_teams(arguments: dict) -> dict:
    """Compare rosters of two teams.

    Note: This function is duplicated in matchup_handlers for organizational purposes.
    Consider consolidating in the future.

    Args:
        arguments: Dict containing:
            - league_key: League identifier
            - team_key_a: First team identifier
            - team_key_b: Second team identifier

    Returns:
        Dict with comparison data
    """
    from src.parsers import parse_team_roster

    league_key = arguments.get("league_key")
    team_key_a = arguments.get("team_key_a")
    team_key_b = arguments.get("team_key_b")

    data_a = await yahoo_api_call(f"team/{team_key_a}/roster")
    data_b = await yahoo_api_call(f"team/{team_key_b}/roster")

    roster_a = parse_team_roster(data_a)
    roster_b = parse_team_roster(data_b)

    return {
        "league_key": league_key,
        "team_a": {"team_key": team_key_a, "roster": roster_a},
        "team_b": {"team_key": team_key_b, "roster": roster_b},
    }


async def handle_ff_get_waiver_wire(arguments: dict) -> dict:
    """Get waiver wire players with comprehensive analysis.

    Args:
        arguments: Dict containing:
            - league_key: League identifier (required)
            - position: Filter by position (default: "all")
            - sort: Sort method - "rank", "points", "owned", "trending" (default: "rank")
            - count: Number of players (default: 30)
            - week: Specific week (optional)
            - team_key: Team key for context (optional)
            - include_analysis: Include detailed analysis (default: False)
            - include_projections: Include projections (default: True)
            - include_external_data: Include Sleeper data (default: True)

    Returns:
        Dict with waiver wire players and optional analysis
    """
    # Validate required parameters
    if not arguments.get("league_key"):
        return {
            "status": "error",
            "error": "league_key is required",
            "message": "Please provide a league_key parameter",
        }

    league_key: str = arguments.get("league_key")  # type: ignore

    # Get and validate optional parameters with proper defaults
    position = arguments.get("position", "all")
    if position is None:
        position = "all"

    sort = arguments.get("sort", "rank")
    if sort not in ["rank", "points", "owned", "trending"]:
        sort = "rank"

    count = arguments.get("count", 30)
    try:
        count = int(count)
        if count < 1:
            count = 30
    except (ValueError, TypeError):
        count = 30

    week = arguments.get("week")
    team_key = arguments.get("team_key")
    include_analysis = arguments.get("include_analysis", False)
    include_projections = arguments.get("include_projections", True)
    include_external_data = arguments.get("include_external_data", True)

    # Fetch basic Yahoo waiver players
    basic_players = await get_waiver_wire_players(league_key, position, sort, count)
    if not basic_players:
        return {
            "status": "success",
            "league_key": league_key,
            "position": position,
            "sort": sort,
            "total_players": 0,
            "players": [],
            "message": "No available players found matching the criteria",
        }

    result = {
        "status": "success",
        "league_key": league_key,
        "position": position,
        "sort": sort,
        "total_players": len(basic_players),
        "players": basic_players,
    }

    needs_enhancement = include_projections or include_external_data or include_analysis

    if not needs_enhancement:
        return result

    try:
        from lineup_optimizer import lineup_optimizer, Player
        from sleeper_api import get_trending_adds, sleeper_client
    except ImportError as exc:
        result["note"] = f"Enhanced data unavailable: {exc}"
        return result

    try:
        # Create payload for optimizer (mimic roster format)
        optimizer_payload = {
            "league_key": league_key,
            "team_key": team_key or "",  # Optional for waivers
            "roster": basic_players,  # Use as 'roster' for parsing
        }
        enhanced_players = await lineup_optimizer.parse_yahoo_roster(optimizer_payload)
        if enhanced_players:
            enhanced_players = await lineup_optimizer.enhance_with_external_data(
                enhanced_players, week=week
            )

            # Add expert advice for waiver wire analysis
            if include_analysis:
                for player in enhanced_players:
                    try:
                        expert_advice = await sleeper_client.get_expert_advice(player.name, week)
                        player.expert_tier = expert_advice.get("tier", "Depth")
                        player.expert_recommendation = expert_advice.get("recommendation", "Bench")
                        player.expert_confidence = expert_advice.get("confidence", 50)
                        player.expert_advice = expert_advice.get("advice", "No analysis available")
                    except Exception:
                        # Continue with default values if expert advice fails
                        player.expert_tier = "Depth"
                        player.expert_recommendation = "Monitor"
                        player.expert_confidence = 50
                        player.expert_advice = f"Expert analysis unavailable"

            # Fetch and merge trending data
            trending = await get_trending_adds(count)
            trending_dict = {p["name"].lower(): p for p in trending}

            def serialize_waiver_player(player: Player) -> Dict[str, Any]:
                base = {
                    "name": player.name,
                    "position": player.position,
                    "team": player.team,
                    "opponent": player.opponent or "N/A",
                    "status": getattr(player, "status", "Available"),
                    "yahoo_projection": player.yahoo_projection if include_projections else None,
                    "sleeper_projection": (
                        player.sleeper_projection if include_projections else None
                    ),
                    "sleeper_id": player.sleeper_id,
                    "sleeper_match_method": player.sleeper_match_method,
                    "floor_projection": player.floor_projection if include_projections else None,
                    "ceiling_projection": (
                        player.ceiling_projection if include_projections else None
                    ),
                    "consistency_score": player.consistency_score,
                    "player_tier": player.player_tier,
                    "matchup_score": player.matchup_score if include_external_data else None,
                    "matchup_description": (
                        player.matchup_description if include_external_data else None
                    ),
                    "trending_score": player.trending_score if include_external_data else None,
                    "risk_level": player.risk_level,
                    "owned_pct": next(
                        (
                            p.get("owned_pct") or 0.0
                            for p in basic_players
                            if p.get("name", "").lower() == player.name.lower()
                        ),
                        0.0,
                    ),
                    "weekly_change": next(
                        (
                            p.get("weekly_change")
                            for p in basic_players
                            if p.get("name", "").lower() == player.name.lower()
                        ),
                        0,
                    ),
                    "injury_status": getattr(player, "injury_status", "Healthy"),
                    "bye": next(
                        (
                            p.get("bye")
                            for p in basic_players
                            if p.get("name", "").lower() == player.name.lower()
                        ),
                        "N/A",
                    ),
                    # Expert advice fields
                    "expert_tier": (
                        getattr(player, "expert_tier", None) if include_analysis else None
                    ),
                    "expert_recommendation": (
                        getattr(player, "expert_recommendation", None) if include_analysis else None
                    ),
                    "expert_confidence": (
                        getattr(player, "expert_confidence", None) if include_analysis else None
                    ),
                    "expert_advice": (
                        getattr(player, "expert_advice", None) if include_analysis else None
                    ),
                }

                # Merge trending
                name_lower = player.name.lower()
                if name_lower in trending_dict:
                    trend = trending_dict[name_lower]
                    base["trending_count"] = trend.get("count", 0)
                    base["trending_position"] = trend.get("position")

                return base

            # Analyze positional scarcity in league for context
            position_scarcity = {}
            if include_analysis:
                try:
                    # Simple scarcity analysis based on ownership and position
                    position_counts = {}

                    for p in basic_players:
                        pos = p.get("position", "Unknown")
                        owned = p.get("owned_pct", 0.0)

                        if pos not in position_counts:
                            position_counts[pos] = {"total": 0, "owned_sum": 0}

                        position_counts[pos]["total"] += 1
                        position_counts[pos]["owned_sum"] += owned

                    # Calculate scarcity scores
                    for pos, data in position_counts.items():
                        avg_owned = data["owned_sum"] / data["total"] if data["total"] > 0 else 0
                        # Higher average ownership = more scarcity
                        scarcity_score = min(avg_owned / 10, 10)  # 0-10 scale
                        position_scarcity[pos] = {
                            "scarcity_score": round(scarcity_score, 1),
                            "avg_ownership": round(avg_owned, 1),
                            "available_count": data["total"],
                        }
                except Exception:
                    # If scarcity analysis fails, continue without it
                    pass

            # Serialize enhanced players with analysis
            enhanced_list = []
            for player in enhanced_players:
                if not player.is_valid():
                    continue

                # Create serialized player data
                base = serialize_waiver_player(player)

                # Add waiver-specific analysis if flagged
                if include_analysis:
                    # Calculate comprehensive waiver priority score
                    expert_confidence = getattr(player, "expert_confidence", 50)
                    proj = (player.yahoo_projection or 0) + (player.sleeper_projection or 0)
                    trend_score = base.get("trending_count", 0)
                    owned = base.get("owned_pct", 0.0)

                    # Position scarcity bonus (0-5 points)
                    pos_scarcity = position_scarcity.get(player.position, {}).get(
                        "scarcity_score", 0
                    )
                    scarcity_bonus = min(pos_scarcity * 0.5, 5)

                    # Waiver-specific scoring algorithm
                    # Base score from expert confidence (35% weight, reduced to add scarcity)
                    confidence_score = expert_confidence * 0.35

                    # Projection score (30% weight)
                    projection_score = min(proj * 2, 30)  # Cap at 30 points

                    # Ownership bonus - lower ownership = higher priority (20% weight)
                    ownership_bonus = max(0, (50 - owned) * 0.4)  # Max 20 points for 0% owned

                    # Trending bonus (10% weight)
                    trending_bonus = min(trend_score * 1.5, 10)  # Cap at 10 points

                    # Final waiver priority score
                    waiver_priority = (
                        confidence_score
                        + projection_score
                        + ownership_bonus
                        + trending_bonus
                        + scarcity_bonus
                    )
                    base["waiver_priority"] = round(waiver_priority, 1)

                    # Enhanced analysis explanation
                    expert_tier = getattr(player, "expert_tier", "Unknown")
                    expert_rec = getattr(player, "expert_recommendation", "Monitor")

                    # Add scarcity context to analysis
                    scarcity_text = ""
                    if pos_scarcity > 7:
                        scarcity_text = f" HIGH SCARCITY at {player.position}!"
                    elif pos_scarcity > 4:
                        scarcity_text = f" Moderate scarcity at {player.position}."

                    base["analysis"] = (
                        f"{expert_tier} tier player with {expert_confidence}% confidence. "
                        f"Recommendation: {expert_rec}. Priority: {base['waiver_priority']}/100 "
                        f"(proj: {proj:.1f}, owned: {owned:.1f}%, trending: {trend_score}){scarcity_text}"
                    )

                    # Add pickup urgency classification (adjusted for scarcity)
                    urgency_threshold = waiver_priority + (
                        scarcity_bonus * 2
                    )  # Boost urgency for scarce positions
                    if urgency_threshold >= 80:
                        base["pickup_urgency"] = "MUST ADD - Elite waiver target"
                    elif urgency_threshold >= 65:
                        base["pickup_urgency"] = "High Priority - Strong pickup"
                    elif urgency_threshold >= 50:
                        base["pickup_urgency"] = "Moderate - Worth a claim"
                    elif urgency_threshold >= 35:
                        base["pickup_urgency"] = "Low Priority - Depth option"
                    else:
                        base["pickup_urgency"] = "Avoid - Better options available"

                    # Add position context
                    base["position_context"] = position_scarcity.get(
                        player.position,
                        {"scarcity_score": 0, "avg_ownership": 0, "available_count": 0},
                    )

                enhanced_list.append(base)
            # Sort by waiver_priority or projection if analysis/projections
            if include_analysis:
                enhanced_list.sort(key=lambda x: x.get("waiver_priority", 0), reverse=True)
            elif include_projections:
                enhanced_list.sort(
                    key=lambda x: (x.get("sleeper_projection") or 0)
                    + (x.get("yahoo_projection") or 0),
                    reverse=True,
                )

            result.update(
                {
                    "enhanced_players": enhanced_list,
                    "analysis_context": {
                        "data_sources": ["Yahoo"] + (["Sleeper"] if include_external_data else []),
                        "includes": {
                            "projections": include_projections,
                            "external_data": include_external_data,
                            "analysis": include_analysis,
                            "expert_advice": include_analysis,  # Expert advice tied to analysis flag
                        },
                        "features": [
                            "Yahoo ownership and change data",
                            "Sleeper projections and rankings" if include_external_data else None,
                            "Matchup analysis" if include_external_data else None,
                            "Expert tier classification" if include_analysis else None,
                            "Waiver priority scoring" if include_analysis else None,
                            "Pickup urgency assessment" if include_analysis else None,
                            "Positional scarcity analysis" if include_analysis else None,
                        ],
                        "algorithm": (
                            {
                                "waiver_priority_weights": {
                                    "expert_confidence": "35%",
                                    "projections": "30%",
                                    "ownership_bonus": "20%",
                                    "trending_bonus": "10%",
                                    "scarcity_bonus": "5%",
                                }
                            }
                            if include_analysis
                            else None
                        ),
                        "position_scarcity": position_scarcity if include_analysis else None,
                        "week": week or "current",
                        "trending_count": len(trending),
                    },
                }
            )
        else:
            result["note"] = "No players could be enhanced"
    except Exception as exc:
        result["note"] = f"Enhancement failed: {exc}. Using basic data."

    return result
