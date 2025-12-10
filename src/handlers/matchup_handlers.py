"""Matchup MCP tool handlers."""

from typing import Any

# These will be injected from main file
get_user_team_key = None
get_user_team_info = None
yahoo_api_call = None
parse_team_roster = None


def _extract_roster_positions(settings_data: dict) -> list:
    """Extract roster positions from Yahoo league settings API response.
    
    Args:
        settings_data: Raw response from Yahoo /league/{key}/settings endpoint
        
    Returns:
        List of dicts like [{"position": "QB", "count": 1}, {"position": "RB", "count": 2}, ...]
    """
    positions = []
    
    try:
        content = settings_data.get("fantasy_content", {})
        league = content.get("league", {})
        
        # League can be a list in Yahoo's response format
        if isinstance(league, list):
            for item in league:
                if isinstance(item, dict) and "settings" in item:
                    settings = item["settings"]
                    if isinstance(settings, list):
                        for setting in settings:
                            if isinstance(setting, dict) and "roster_positions" in setting:
                                roster_data = setting["roster_positions"]
                                positions = _parse_roster_data(roster_data)
                                break
                    elif isinstance(settings, dict) and "roster_positions" in settings:
                        positions = _parse_roster_data(settings["roster_positions"])
                    if positions:
                        break
        elif isinstance(league, dict) and "settings" in league:
            # Alternative structure
            settings = league["settings"]
            if "roster_positions" in settings:
                positions = _parse_roster_data(settings["roster_positions"])
    except Exception:
        pass  # Return empty list on error
    
    return positions


def _parse_roster_data(roster_data) -> list:
    """Parse roster_positions data into standardized format."""
    positions = []
    
    if isinstance(roster_data, list):
        for pos_item in roster_data:
            if isinstance(pos_item, dict):
                if "roster_position" in pos_item:
                    pos_info = pos_item["roster_position"]
                    positions.append({
                        "position": pos_info.get("position", ""),
                        "count": int(pos_info.get("count", 1)),
                    })
                elif "position" in pos_item:
                    positions.append({
                        "position": pos_item.get("position", ""),
                        "count": int(pos_item.get("count", 1)),
                    })
    elif isinstance(roster_data, dict):
        # Sometimes it's a dict with numbered keys
        for key, value in roster_data.items():
            if key == "count":
                continue
            if isinstance(value, dict):
                if "roster_position" in value:
                    pos_info = value["roster_position"]
                    positions.append({
                        "position": pos_info.get("position", ""),
                        "count": int(pos_info.get("count", 1)),
                    })
    
    return positions


async def handle_ff_get_matchup(arguments: dict) -> dict:
    """Get matchup information for a team in a specific week.

    Args:
        arguments: Dict containing:
            - league_key: League identifier
            - week: Week number (optional, defaults to current)

    Returns:
        Dict with matchup data
    """
    league_key = arguments.get("league_key")
    week = arguments.get("week")
    team_key = await get_user_team_key(league_key)

    if not team_key:
        return {"error": f"Could not find your team in league {league_key}"}

    week_param = f";week={week}" if week else ""
    data = await yahoo_api_call(f"team/{team_key}/matchups{week_param}")
    return {
        "league_key": league_key,
        "team_key": team_key,
        "week": week or "current",
        "message": "Matchup data retrieved",
        "raw_matchups": data,
    }


async def handle_ff_compare_teams(arguments: dict) -> dict:
    """Compare rosters of two teams.

    Args:
        arguments: Dict containing:
            - league_key: League identifier
            - team_key_a: First team identifier
            - team_key_b: Second team identifier

    Returns:
        Dict with comparison data
    """
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


async def handle_ff_build_lineup(arguments: dict) -> dict:
    """Build optimal lineup using advanced analytics.

    Args:
        arguments: Dict containing:
            - league_key: League identifier
            - week: Week number (optional)
            - strategy: "balanced", "floor", or "ceiling" (default: "balanced")
            - use_llm: Use LLM for additional insights (default: False)

    Returns:
        Dict with optimal lineup and recommendations
    """
    league_key = arguments.get("league_key")
    week = arguments.get("week")
    strategy = arguments.get("strategy", "balanced")
    use_llm = arguments.get("use_llm", False)

    team_key = await get_user_team_key(league_key)
    if not team_key:
        return {"error": f"Could not find your team in league {league_key}"}

    try:
        roster_data = await yahoo_api_call(f"team/{team_key}/roster")
        try:
            from lineup_optimizer import lineup_optimizer
        except ImportError as exc:
            return {
                "error": f"Lineup optimizer unavailable: {exc}",
                "suggestion": "Please check lineup_optimizer.py dependencies",
                "league_key": league_key,
                "team_key": team_key,
            }

        players = await lineup_optimizer.parse_yahoo_roster(roster_data)
        if not players:
            return {
                "error": "Failed to parse Yahoo roster data",
                "league_key": league_key,
                "team_key": team_key,
                "suggestion": "Check roster data format or try refreshing",
            }

        # Fetch league settings to get roster configuration
        roster_positions = None
        try:
            settings_data = await yahoo_api_call(f"league/{league_key}/settings")
            roster_positions = _extract_roster_positions(settings_data)
        except Exception as e:
            # Log but don't fail - optimizer will use defaults
            import logging
            logging.getLogger(__name__).warning(
                f"Could not fetch roster settings for {league_key}: {e}. Using defaults."
            )

        players = await lineup_optimizer.enhance_with_external_data(players, week=week)
        optimization = await lineup_optimizer.optimize_lineup_smart(
            players,
            strategy,
            week,
            use_llm,
            roster_positions=roster_positions,
        )
        if optimization["status"] == "error":
            return {
                "status": "error",
                "error": "Lineup optimization failed",
                "league_key": league_key,
                "team_key": team_key,
                "errors": optimization.get("errors", []),
                "details": optimization.get("errors", []),
                "data_quality": optimization.get("data_quality", {}),
            }

        starters_formatted = {}
        for pos, player in optimization["starters"].items():
            starters_formatted[pos] = {
                "name": player.name,
                "tier": player.player_tier.upper() if player.player_tier else "UNKNOWN",
                "team": player.team,
                "opponent": player.opponent,
                "matchup_score": player.matchup_score,
                "matchup": player.matchup_description,
                "composite_score": round(player.composite_score, 1),
                "yahoo_proj": (
                    round(player.yahoo_projection, 1) if player.yahoo_projection else None
                ),
                "sleeper_proj": (
                    round(player.sleeper_projection, 1) if player.sleeper_projection else None
                ),
                "trending": (
                    f"{player.trending_score:,} adds" if player.trending_score > 0 else None
                ),
                "floor": round(player.floor_projection, 1) if player.floor_projection else None,
                "ceiling": (
                    round(player.ceiling_projection, 1) if player.ceiling_projection else None
                ),
            }

        bench_formatted = [
            {
                "name": player.name,
                "position": player.position,
                "opponent": player.opponent,
                "composite_score": round(player.composite_score, 1),
                "matchup_score": player.matchup_score,
                "tier": player.player_tier.upper() if player.player_tier else "UNKNOWN",
            }
            for player in optimization["bench"][:5]
        ]

        result: dict[str, Any] = {
            "status": optimization["status"],
            "league_key": league_key,
            "team_key": team_key,
            "week": week or "current",
            "strategy": strategy,
            "optimal_lineup": starters_formatted,
            "bench": bench_formatted,
            "recommendations": optimization["recommendations"],
            "errors": optimization.get("errors", []),
            "analysis": {
                "total_players": optimization["data_quality"]["total_players"],
                "valid_players": optimization["data_quality"]["valid_players"],
                "players_with_projections": optimization["data_quality"][
                    "players_with_projections"
                ],
                "players_with_matchup_data": optimization["data_quality"][
                    "players_with_matchup_data"
                ],
                "strategy_used": optimization["strategy_used"],
                "data_sources": [
                    "Yahoo projections",
                    "Sleeper rankings",
                    "Matchup analysis",
                    "Trending data",
                ],
            },
        }
        if optimization.get("errors"):
            result["warnings"] = optimization["errors"]
        return result
    except Exception as exc:
        return {
            "error": f"Unexpected error during lineup optimization: {exc}",
            "league_key": league_key,
            "team_key": team_key,
            "suggestion": "Try again or check system logs for details",
        }
