"""Matchup MCP tool handlers."""

from typing import Any, Optional

# These will be injected from main file
get_user_team_key = None
get_user_team_info = None
yahoo_api_call = None
parse_team_roster = None


def _matchup_week(entry: Any) -> Optional[int]:
    matchup = entry.get("matchup") if isinstance(entry, dict) else None
    try:
        return int(matchup.get("week"))
    except (AttributeError, TypeError, ValueError):
        return None


def _select_matchup_week(data: Any, week: Optional[int]) -> tuple[Any, Optional[int]]:
    """Trim a team/matchups response to a single week.

    Yahoo returns every week of the season. Keep only the requested week, or the
    current one when no week is given: the first matchup that is not final, falling
    back to the last week once the season is over.

    Returns a copy (the original may be a cached response) and the week kept. If the
    payload is not in the expected shape it is returned unchanged.
    """
    content = data.get("fantasy_content") if isinstance(data, dict) else None
    team = content.get("team") if isinstance(content, dict) else None
    if not isinstance(team, list):
        return data, week

    for index, part in enumerate(team):
        if not isinstance(part, dict) or not isinstance(part.get("matchups"), dict):
            continue

        matchups = part["matchups"]
        entries = [
            matchups[key]
            for key in sorted((k for k in matchups if str(k).isdigit()), key=int)
            if _matchup_week(matchups[key]) is not None
        ]
        if not entries:
            return data, week

        if week is not None:
            chosen = [entry for entry in entries if _matchup_week(entry) == week]
        else:
            upcoming = [entry for entry in entries if entry["matchup"].get("status") != "postevent"]
            chosen = [upcoming[0] if upcoming else entries[-1]]
            week = _matchup_week(chosen[0])

        trimmed = {str(i): entry for i, entry in enumerate(chosen)}
        trimmed["count"] = len(chosen)
        new_team = list(team)
        new_team[index] = {**part, "matchups": trimmed}
        return {**data, "fantasy_content": {**content, "team": new_team}}, week

    return data, week


async def handle_ff_get_matchup(arguments: dict) -> dict:
    """Get matchup information for a team in a specific week.

    Args:
        arguments: Dict containing:
            - league_key: League identifier
            - week: Week number (optional, defaults to current)

    Returns:
        Dict with matchup data for that single week
    """
    league_key = arguments.get("league_key")
    try:
        week = int(arguments["week"]) if arguments.get("week") else None
    except (TypeError, ValueError):
        return {"error": "week must be a number"}
    team_key = await get_user_team_key(league_key)

    if not team_key:
        return {"error": f"Could not find your team in league {league_key}"}

    # One cached call covers every week; the week is picked out below.
    data = await yahoo_api_call(f"team/{team_key}/matchups")
    data, resolved_week = _select_matchup_week(data, week)
    return {
        "league_key": league_key,
        "team_key": team_key,
        "week": resolved_week or "current",
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

        players = await lineup_optimizer.enhance_with_external_data(players, week=week)

        # The league's own starting slots (3 WR, W/R flex, IDP, ...); defaults if unreadable.
        roster_slots = []
        try:
            from lineup_optimizer import parse_roster_slots

            roster_slots = parse_roster_slots(await yahoo_api_call(f"league/{league_key}/settings"))
        except Exception:
            roster_slots = []

        optimization = await lineup_optimizer.optimize_lineup_smart(
            players,
            strategy,
            week,
            use_llm,
            roster_slots=roster_slots,
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
                "position": player.raw.get("display_position") or player.position,
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
