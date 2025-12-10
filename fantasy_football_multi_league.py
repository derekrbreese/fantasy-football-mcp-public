#!/usr/bin/env python3
"""
Fantasy Football MCP Server - Multi-League Support
"""

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Import extracted modules
from src.api import get_access_token, refresh_yahoo_token, set_access_token, yahoo_api_call
from src.parsers import parse_team_roster, parse_yahoo_free_agent_players
from src.services import analyze_reddit_sentiment

# Import rate limiting and caching utilities
from src.api.yahoo_utils import rate_limiter, response_cache

# Import bye week utilities
from src.utils.bye_weeks import get_bye_week_with_fallback

# Import all handlers from the handlers module
from pathlib import Path

# Find project root and load .env from there
PROJECT_ROOT = Path(__file__).parent.absolute()
ENV_FILE_PATH = PROJECT_ROOT / ".env"

from src.handlers import (
    handle_ff_analyze_draft_state,
    handle_ff_analyze_reddit_sentiment,
    handle_ff_build_lineup,
    handle_ff_clear_cache,
    handle_ff_compare_teams,
    handle_ff_get_api_status,
    handle_ff_get_draft_rankings,
    handle_ff_get_draft_recommendation,
    handle_ff_get_draft_results,
    handle_ff_get_league_info,
    handle_ff_get_leagues,
    handle_ff_get_matchup,
    handle_ff_get_players,
    handle_ff_get_roster,
    handle_ff_get_standings,
    handle_ff_get_teams,
    handle_ff_get_waiver_wire,
    handle_ff_refresh_token,
    inject_draft_dependencies,
    inject_league_helpers,
    inject_matchup_dependencies,
    inject_player_dependencies,
    inject_roster_dependencies,
)

# Draft functionality is built-in (no complex imports needed)
DRAFT_AVAILABLE = True

# Load environment from project root
load_dotenv(dotenv_path=ENV_FILE_PATH)

# Initialize access token in the API module
if os.getenv("YAHOO_ACCESS_TOKEN"):
    set_access_token(os.getenv("YAHOO_ACCESS_TOKEN"))

# Create server instance
server = Server("fantasy-football")

# Cache for leagues
LEAGUES_CACHE = {}


async def discover_leagues() -> dict[str, dict[str, Any]]:
    """Discover all active NFL leagues for the authenticated user."""
    global LEAGUES_CACHE

    if LEAGUES_CACHE:
        return LEAGUES_CACHE

    # Get current NFL leagues (game key 461 for 2025)
    data = await yahoo_api_call("users;use_login=1/games;game_keys=nfl/leagues")

    leagues = {}
    try:
        users = data.get("fantasy_content", {}).get("users", {})

        if "0" in users:
            user = users["0"]["user"]

            if isinstance(user, list):
                for item in user:
                    if isinstance(item, dict) and "games" in item:
                        games = item["games"]

                        if "0" in games:  # First game (NFL)
                            game = games["0"]["game"]
                            if isinstance(game, list):
                                for g in game:
                                    if isinstance(g, dict) and "leagues" in g:
                                        league_data = g["leagues"]

                                        for key in league_data:
                                            if key != "count" and isinstance(
                                                league_data[key], dict
                                            ):
                                                if "league" in league_data[key]:
                                                    league_info = league_data[key]["league"]
                                                    if (
                                                        isinstance(league_info, list)
                                                        and len(league_info) > 0
                                                    ):
                                                        league_dict = league_info[0]

                                                        league_key = league_dict.get(
                                                            "league_key", ""
                                                        )
                                                        leagues[league_key] = {
                                                            "key": league_key,
                                                            "id": league_dict.get("league_id", ""),
                                                            "name": league_dict.get(
                                                                "name", "Unknown"
                                                            ),
                                                            "season": league_dict.get(
                                                                "season", 2025
                                                            ),
                                                            "num_teams": league_dict.get(
                                                                "num_teams", 0
                                                            ),
                                                            "scoring_type": league_dict.get(
                                                                "scoring_type", "head"
                                                            ),
                                                            "current_week": league_dict.get(
                                                                "current_week", 1
                                                            ),
                                                            "is_finished": league_dict.get(
                                                                "is_finished", 0
                                                            ),
                                                        }
    except Exception:
        pass  # Silently handle error to not interfere with MCP protocol

    LEAGUES_CACHE = leagues
    return leagues


async def get_user_team_info(league_key: Optional[str]) -> Optional[dict]:
    if not league_key:
        return None
    """Get the user's team details in a league.

    Normalizes manager entries and `is_owned_by_current_login` flags so the
    caller can reliably identify which team belongs to the authenticated user.
    """
    try:
        data = await yahoo_api_call(f"league/{league_key}/teams")

        # Get user's GUID from environment
        user_guid = os.getenv("YAHOO_GUID", "your_yahoo_guid_here")

        # Parse to find user's team
        league = data.get("fantasy_content", {}).get("league", [])

        if len(league) > 1 and isinstance(league[1], dict) and "teams" in league[1]:
            teams = league[1]["teams"]

            for key in teams:
                if key != "count" and isinstance(teams[key], dict):
                    if "team" in teams[key]:
                        team_array = teams[key]["team"]

                        if isinstance(team_array, list) and len(team_array) > 0:
                            # The team data is in the first element
                            team_data = team_array[0]

                            if isinstance(team_data, list):
                                team_key = None
                                team_name = None
                                is_users_team = False
                                draft_grade = None
                                draft_position = None

                                # Parse each element in the team data
                                for element in team_data:
                                    if isinstance(element, dict):
                                        # Check for team key
                                        if "team_key" in element:
                                            team_key = element["team_key"]

                                        # Get team name
                                        if "name" in element:
                                            team_name = element["name"]

                                        # Get draft grade
                                        if "draft_grade" in element:
                                            draft_grade = element["draft_grade"]

                                        # Get draft position
                                        if "draft_position" in element:
                                            draft_position = element["draft_position"]

                                        # Check if owned by current login (API may return int, bool or string)
                                        owned_flag = element.get("is_owned_by_current_login")
                                        if str(owned_flag) == "1" or owned_flag is True:
                                            is_users_team = True

                                        # Also check by GUID
                                        if "managers" in element:
                                            managers = element["managers"]
                                            if isinstance(managers, dict):
                                                managers = [
                                                    m
                                                    for key, m in managers.items()
                                                    if key != "count"
                                                ]
                                            if managers:
                                                mgr = managers[0].get("manager", {})
                                                if mgr.get("guid") == user_guid:
                                                    is_users_team = True

                                if is_users_team and team_key:
                                    return {
                                        "team_key": team_key,
                                        "team_name": team_name,
                                        "draft_grade": draft_grade,
                                        "draft_position": draft_position,
                                    }

        return None
    except Exception:
        # Silently handle error to not interfere with MCP protocol
        return None


async def get_user_team_key(league_key: Optional[str]) -> Optional[str]:
    if not league_key:
        return None
    """Get the user's team key in a specific league (legacy function for compatibility)."""
    team_info = await get_user_team_info(league_key)
    return team_info["team_key"] if team_info else None


async def get_waiver_wire_players(
    league_key: str, position: str = "all", sort: str = "rank", count: int = 30
) -> list[dict]:
    """Get available waiver wire players with detailed stats."""
    try:
        # Build the API call with filters
        pos_filter = f";position={position}" if position != "all" else ""
        sort_type = {
            "rank": "OR",  # Overall rank
            "points": "PTS",  # Points
            "owned": "O",  # Ownership %
            "trending": "A",  # Added %
        }.get(sort, "OR")

        endpoint = (
            f"league/{league_key}/players;status=A{pos_filter};sort={sort_type};count={count}"
        )
        data = await yahoo_api_call(endpoint)

        players = []
        league = data.get("fantasy_content", {}).get("league", [])

        # Players are in the second element of the league array
        if len(league) > 1 and isinstance(league[1], dict) and "players" in league[1]:
            players_data = league[1]["players"]

            for key in players_data:
                if key != "count" and isinstance(players_data[key], dict):
                    if "player" in players_data[key]:
                        player_array = players_data[key]["player"]

                        # Player data is in nested array structure
                        if isinstance(player_array, list) and len(player_array) > 0:
                            player_data = player_array[0]

                            if isinstance(player_data, list):
                                player_info = {}

                                for element in player_data:
                                    if isinstance(element, dict):
                                        # Basic info
                                        if "name" in element:
                                            player_info["name"] = element["name"]["full"]
                                        if "player_key" in element:
                                            player_info["player_key"] = element["player_key"]
                                        if "editorial_team_abbr" in element:
                                            player_info["team"] = element["editorial_team_abbr"]
                                        if "display_position" in element:
                                            player_info["position"] = element["display_position"]
                                        
                                        # Extract bye week with fallback to static data
                                        api_bye_week = None
                                        if "bye_weeks" in element:
                                            bye_weeks_data = element["bye_weeks"]
                                            if isinstance(bye_weeks_data, dict) and "week" in bye_weeks_data:
                                                bye_week = bye_weeks_data.get("week")
                                                # Validate bye week is a valid week number (1-18)
                                                if bye_week and str(bye_week).isdigit():
                                                    bye_num = int(bye_week)
                                                    if 1 <= bye_num <= 18:
                                                        api_bye_week = bye_num
                                        
                                        # Use fallback utility to get bye week (tries API first, then static data)
                                        team_abbr = element.get("editorial_team_abbr", "")
                                        player_info["bye"] = get_bye_week_with_fallback(team_abbr, api_bye_week)

                                        # Ownership data
                                        if "ownership" in element:
                                            ownership = element["ownership"]
                                            player_info["owned_pct"] = ownership.get(
                                                "ownership_percentage", 0
                                            )
                                            player_info["weekly_change"] = ownership.get(
                                                "weekly_change", 0
                                            )

                                        # Injury status
                                        if "status" in element:
                                            player_info["injury_status"] = element["status"]
                                        if "status_full" in element:
                                            player_info["injury_detail"] = element["status_full"]

                                if player_info.get("name"):
                                    # Ensure all expected fields are present with defaults
                                    player_info.setdefault("team", "FA")  # Free Agent if no team
                                    player_info.setdefault(
                                        "owned_pct", 0
                                    )  # 0% if no ownership data
                                    player_info.setdefault(
                                        "weekly_change", 0
                                    )  # No change if no data
                                    player_info.setdefault(
                                        "injury_status", "Healthy"
                                    )  # Assume healthy if not specified
                                    players.append(player_info)

        return players
    except Exception:
        return []


async def get_draft_rankings(
    league_key: Optional[str] = None, position: str = "all", count: int = 50
) -> list[dict]:
    """Get pre-draft rankings with ADP data."""
    try:
        # If no league key provided, get the first available league
        if not league_key:
            leagues = await discover_leagues()
            if leagues:
                league_key = list(leagues.keys())[0]
            else:
                return []  # No leagues available

        pos_filter = f";position={position}" if position != "all" else ""

        # Get all players sorted by rank for the specified league
        endpoint = f"league/{league_key}/players{pos_filter};sort=OR;count={count}"
        data = await yahoo_api_call(endpoint)

        players = []
        league = data.get("fantasy_content", {}).get("league", [])

        # Players are in the second element of the league array
        if len(league) > 1 and isinstance(league[1], dict) and "players" in league[1]:
            players_data = league[1]["players"]

            for key in players_data:
                if key != "count" and isinstance(players_data[key], dict):
                    if "player" in players_data[key]:
                        player_array = players_data[key]["player"]

                        # Player data is in nested array structure
                        if isinstance(player_array, list) and len(player_array) > 0:
                            player_data = player_array[0]

                            if isinstance(player_data, list):
                                player_info = {}
                                rank = int(key) + 1  # Use the key as rank

                                for element in player_data:
                                    if isinstance(element, dict):
                                        if "name" in element:
                                            player_info["name"] = element["name"]["full"]
                                        if "editorial_team_abbr" in element:
                                            player_info["team"] = element["editorial_team_abbr"]
                                        if "display_position" in element:
                                            player_info["position"] = element["display_position"]
                                        
                                        # Extract bye week with fallback to static data
                                        api_bye_week = None
                                        if "bye_weeks" in element:
                                            bye_weeks_data = element["bye_weeks"]
                                            if isinstance(bye_weeks_data, dict) and "week" in bye_weeks_data:
                                                bye_week = bye_weeks_data.get("week")
                                                # Validate bye week is a valid week number (1-18)
                                                if bye_week and str(bye_week).isdigit():
                                                    bye_num = int(bye_week)
                                                    if 1 <= bye_num <= 18:
                                                        api_bye_week = bye_num
                                        
                                        # Use fallback utility to get bye week (tries API first, then static data)
                                        team_abbr = element.get("editorial_team_abbr", "")
                                        player_info["bye"] = get_bye_week_with_fallback(team_abbr, api_bye_week)

                                        # Draft data if available
                                        if "draft_analysis" in element:
                                            draft = element["draft_analysis"]
                                            player_info["average_draft_position"] = draft.get(
                                                "average_pick", rank
                                            )
                                            player_info["average_round"] = draft.get(
                                                "average_round", "N/A"
                                            )
                                            player_info["average_cost"] = draft.get(
                                                "average_cost", "N/A"
                                            )
                                            player_info["percent_drafted"] = draft.get(
                                                "percent_drafted", 0
                                            )
                                        else:
                                            # Use rank as ADP if no draft data
                                            player_info["rank"] = rank

                                if player_info.get("name"):
                                    players.append(player_info)

        # Sort by ADP if available
        players.sort(
            key=lambda x: (
                float(x.get("average_draft_position", 999))
                if x.get("average_draft_position") != "N/A"
                else 999
            )
        )

        return players
    except Exception:
        return []


async def get_all_teams_info(league_key: str) -> list[dict]:
    """Get all teams information including draft data."""
    try:
        data = await yahoo_api_call(f"league/{league_key}/teams")

        teams_list = []
        league = data.get("fantasy_content", {}).get("league", [])

        if len(league) > 1 and isinstance(league[1], dict) and "teams" in league[1]:
            teams = league[1]["teams"]

            for key in teams:
                if key != "count" and isinstance(teams[key], dict):
                    if "team" in teams[key]:
                        team_array = teams[key]["team"]

                        if isinstance(team_array, list) and len(team_array) > 0:
                            team_data = team_array[0]

                            if isinstance(team_data, list):
                                team_info = {}

                                for element in team_data:
                                    if isinstance(element, dict):
                                        if "team_key" in element:
                                            team_info["team_key"] = element["team_key"]
                                        if "team_id" in element:
                                            team_info["team_id"] = element["team_id"]
                                        if "name" in element:
                                            team_info["name"] = element["name"]
                                        if "draft_grade" in element:
                                            team_info["draft_grade"] = element["draft_grade"]
                                        if "draft_position" in element:
                                            team_info["draft_position"] = element["draft_position"]
                                        if "draft_recap_url" in element:
                                            team_info["draft_recap_url"] = element[
                                                "draft_recap_url"
                                            ]
                                        if "number_of_moves" in element:
                                            team_info["moves"] = element["number_of_moves"]
                                        if "number_of_trades" in element:
                                            team_info["trades"] = element["number_of_trades"]
                                        if "managers" in element:
                                            managers = element["managers"]
                                            if managers and len(managers) > 0:
                                                mgr = managers[0].get("manager", {})
                                                team_info["manager"] = mgr.get(
                                                    "nickname", "Unknown"
                                                )

                                if team_info.get("team_key"):
                                    teams_list.append(team_info)

        # Sort by draft position if available
        teams_list.sort(key=lambda x: x.get("draft_position", 999))
        return teams_list

    except Exception:
        return []


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available fantasy football tools."""
    base_tools = [
        Tool(
            name="ff_get_leagues",
            description="Get all your fantasy football leagues",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ff_get_league_info",
            description="Get detailed information about a specific league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX'). Use ff_get_leagues to get available keys.",
                    }
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_standings",
            description="Get standings for a specific league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    }
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_teams",
            description="Get all teams in a specific league with basic information",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    }
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_roster",
            description="Get your team roster in a specific league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "team_key": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional team key if not the logged-in team",
                    },
                    "week": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Week for projections and analysis (optional, defaults to current)",
                    },
                    "data_level": {
                        "type": "string",
                        "description": "Data detail level: 'basic', 'standard', 'enhanced'",
                        "enum": ["basic", "standard", "enhanced"],
                        "default": "standard",
                    },
                    "include_analysis": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include basic roster analysis",
                        "default": False,
                    },
                    "include_projections": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include projections from Yahoo and Sleeper",
                        "default": True,
                    },
                    "include_external_data": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include Sleeper data, trending, and matchups",
                        "default": True,
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_matchup",
            description="Get matchup for a specific week in a league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "week": {
                        "type": "integer",
                        "description": "Week number (optional, defaults to current week)",
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_players",
            description="Get available free agent players in a league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "position": {
                        "type": "string",
                        "description": "Position filter (QB, RB, WR, TE, K, DEF)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of players to return",
                        "default": 10,
                    },
                    "sort": {
                        "type": "string",
                        "description": "Sort by: 'rank', 'points', 'owned', 'trending'",
                        "enum": ["rank", "points", "owned", "trending"],
                        "default": "rank",
                    },
                    "week": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Week for projections and analysis (optional, defaults to current)",
                    },
                    "include_analysis": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include basic analysis and rankings",
                        "default": False,
                    },
                    "include_expert_analysis": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include expert analysis and recommendations",
                        "default": False,
                    },
                    "include_projections": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include projections from Yahoo and Sleeper",
                        "default": True,
                    },
                    "include_external_data": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include Sleeper data, trending, and matchups",
                        "default": True,
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_compare_teams",
            description="Compare two teams' rosters within a league",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "team_key_a": {
                        "type": "string",
                        "description": "First team key to compare",
                    },
                    "team_key_b": {
                        "type": "string",
                        "description": "Second team key to compare",
                    },
                },
                "required": ["league_key", "team_key_a", "team_key_b"],
            },
        ),
        Tool(
            name="ff_build_lineup",
            description="Build optimal lineup from your roster using strategy-based optimization and positional constraints",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "week": {
                        "type": "integer",
                        "description": "Week number (optional, defaults to current week)",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Strategy: 'conservative', 'aggressive', or 'balanced' (default: balanced)",
                        "enum": ["conservative", "aggressive", "balanced"],
                    },
                    "use_llm": {
                        "type": "boolean",
                        "description": "Use LLM-based optimization instead of mathematical formulas (default: false)",
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_refresh_token",
            description="Refresh the Yahoo API access token when it expires",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ff_get_draft_results",
            description="Get draft results showing all teams with their draft positions and grades",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "team_key": {
                        "type": "string",
                        "description": "Optional team key if not the logged-in team",
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_waiver_wire",
            description="Get top available waiver wire players with detailed stats and projections",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (e.g., 'nfl.l.XXXXXX')",
                    },
                    "position": {
                        "type": "string",
                        "description": "Position filter (QB, RB, WR, TE, K, DEF, or 'all')",
                        "enum": ["QB", "RB", "WR", "TE", "K", "DEF", "all"],
                    },
                    "sort": {
                        "type": "string",
                        "description": "Sort by: 'rank', 'points', 'owned', 'trending'",
                        "enum": ["rank", "points", "owned", "trending"],
                        "default": "rank",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of players to return (default: 30)",
                        "default": 30,
                    },
                    "week": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Week for projections and analysis (optional, defaults to current)",
                    },
                    "team_key": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional team key for context (e.g., waiver priority)",
                    },
                    "include_analysis": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include basic waiver priority analysis",
                        "default": False,
                    },
                    "include_projections": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include projections from Yahoo and Sleeper",
                        "default": True,
                    },
                    "include_external_data": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": "Include Sleeper data, trending, and matchups",
                        "default": True,
                    },
                },
                "required": ["league_key"],
            },
        ),
        Tool(
            name="ff_get_api_status",
            description="Get Yahoo API rate limit status and cache statistics",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="ff_clear_cache",
            description="Clear the API response cache",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Optional pattern to match (e.g., 'standings', 'roster'). Clears all if not provided.",
                    }
                },
            },
        ),
        Tool(
            name="ff_get_draft_rankings",
            description="Get pre-draft player rankings and ADP (Average Draft Position)",
            inputSchema={
                "type": "object",
                "properties": {
                    "league_key": {
                        "type": "string",
                        "description": "League key (optional, uses first available league if not provided)",
                    },
                    "position": {
                        "type": "string",
                        "description": "Position filter (QB, RB, WR, TE, K, DEF, or 'all')",
                        "enum": ["QB", "RB", "WR", "TE", "K", "DEF", "all"],
                        "default": "all",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of players to return (default: 50)",
                        "default": 50,
                    },
                },
                "required": [],
            },
        ),
    ]

    # Add draft tools if available
    if DRAFT_AVAILABLE:
        draft_tools = [
            Tool(
                name="ff_get_draft_recommendation",
                description="Get AI-powered draft recommendations for live fantasy football drafts",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "league_key": {
                            "type": "string",
                            "description": "League key (e.g., 'nfl.l.XXXXXX')",
                        },
                        "strategy": {
                            "type": "string",
                            "description": "Draft strategy: 'conservative', 'aggressive', or 'balanced' (default: balanced)",
                            "enum": ["conservative", "aggressive", "balanced"],
                            "default": "balanced",
                        },
                        "num_recommendations": {
                            "type": "integer",
                            "description": "Number of top recommendations to return (1-20, default: 10)",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 10,
                        },
                        "current_pick": {
                            "type": "integer",
                            "description": "Current overall pick number (optional)",
                        },
                    },
                    "required": ["league_key"],
                },
            ),
            Tool(
                name="ff_analyze_draft_state",
                description="Analyze current draft state including roster needs and strategic insights",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "league_key": {
                            "type": "string",
                            "description": "League key (e.g., 'nfl.l.XXXXXX')",
                        },
                        "strategy": {
                            "type": "string",
                            "description": "Draft strategy for analysis: 'conservative', 'aggressive', or 'balanced' (default: balanced)",
                            "enum": ["conservative", "aggressive", "balanced"],
                            "default": "balanced",
                        },
                    },
                    "required": ["league_key"],
                },
            ),
            Tool(
                name="ff_analyze_reddit_sentiment",
                description="Analyze Reddit sentiment for fantasy football players to help with Start/Sit decisions",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "players": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of player names to analyze (e.g., ['Josh Allen', 'Jared Goff'])",
                        },
                        "time_window_hours": {
                            "type": "integer",
                            "description": "How far back to look for Reddit posts (default: 48 hours)",
                            "default": 48,
                        },
                    },
                    "required": ["players"],
                },
            ),
        ]
        return base_tools + draft_tools

    return base_tools


TOOL_HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "ff_get_leagues": handle_ff_get_leagues,
    "ff_get_league_info": handle_ff_get_league_info,
    "ff_get_standings": handle_ff_get_standings,
    "ff_get_teams": handle_ff_get_teams,
    "ff_get_roster": handle_ff_get_roster,
    "ff_get_roster_with_projections": handle_ff_get_roster,
    "ff_get_matchup": handle_ff_get_matchup,
    "ff_get_players": handle_ff_get_players,
    "ff_compare_teams": handle_ff_compare_teams,
    "ff_build_lineup": handle_ff_build_lineup,
    "ff_refresh_token": handle_ff_refresh_token,
    "ff_get_api_status": handle_ff_get_api_status,
    "ff_clear_cache": handle_ff_clear_cache,
    "ff_get_draft_results": handle_ff_get_draft_results,
    "ff_get_waiver_wire": handle_ff_get_waiver_wire,
    "ff_get_draft_rankings": handle_ff_get_draft_rankings,
    "ff_get_draft_recommendation": handle_ff_get_draft_recommendation,
    "ff_analyze_draft_state": handle_ff_analyze_draft_state,
    "ff_analyze_reddit_sentiment": handle_ff_analyze_reddit_sentiment,
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a fantasy football tool via modular handlers."""
    original_arguments = dict(arguments)
    handler_args = {k: v for k, v in original_arguments.items() if k != "debug"}
    debug_flag = original_arguments.get("debug") is True
    debug_msgs: list[str] = []
    if debug_flag:
        debug_msgs.append(f"debug: call_tool entered for {name}")

    try:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            result: Any = {"error": f"Unknown tool: {name}"}
        else:
            result = await handler(handler_args)

        if isinstance(result, str) and result.strip() == "0":
            result = {
                "status": "error",
                "message": "Internal legacy layer produced sentinel '0' string",
                "tool": name,
                "stage": "legacy.call_tool.guard",
            }

        # Ensure result is always a dict for consistent handling
        if isinstance(result, str):
            result = {"content": result}

        if debug_flag:
            safe_args = {
                key: value
                for key, value in handler_args.items()
                if not key.lower().endswith("token")
            }
            debug_msgs.append(f"debug: sanitized arguments -> {sorted(safe_args.keys())}")
            result["_debug"] = {
                "messages": debug_msgs,
                "tool": name,
                "arguments": safe_args,
            }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:  # pragma: no cover - defensive catch
        error_result = {
            "error": str(exc),
            "tool": name,
            "arguments": original_arguments,
        }
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def get_draft_recommendation_simple(
    league_key: str, strategy: str, num_recommendations: int, current_pick: Optional[int] = None
) -> dict:
    """Simplified draft recommendation using available data."""
    try:
        # Get available players using existing waiver wire function
        available_players = await get_waiver_wire_players(league_key, count=100)
        draft_rankings = await get_draft_rankings(league_key, count=50)

        # Simple scoring based on rankings and availability
        recommendations = []

        # Create a quick lookup for available players
        available_names = {p.get("name", "").lower() for p in available_players}

        for player in draft_rankings:
            player_name = player.get("name", "").lower()
            if player_name in available_names:
                # Simple scoring based on strategy
                rank = player.get("rank", 999)
                base_score = max(0, 100 - rank)

                if strategy == "conservative":
                    # Prefer higher-ranked (safer) picks
                    score = base_score + (10 if rank <= 24 else 0)
                    reasoning = f"Rank #{rank}, conservative choice (proven player)"
                elif strategy == "aggressive":
                    # Prefer potential breakouts (lower owned %)
                    owned_pct = next(
                        (
                            p.get("owned_pct", 50)
                            for p in available_players
                            if p.get("name", "").lower() == player_name
                        ),
                        50,
                    )
                    upside_bonus = max(0, 20 - (owned_pct / 5))  # Bonus for lower ownership
                    score = base_score + upside_bonus
                    reasoning = f"Rank #{rank}, high upside potential ({owned_pct}% owned)"
                else:  # balanced
                    score = base_score + (5 if rank <= 50 else 0)
                    reasoning = f"Rank #{rank}, balanced value pick"

                recommendations.append({"player": player, "score": score, "reasoning": reasoning})

        # Sort by score and take top N
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        top_picks = recommendations[:num_recommendations]

        return {
            "status": "success",
            "league_key": league_key,
            "strategy": strategy,
            "current_pick": current_pick,
            "recommendations": top_picks,
            "total_analyzed": len(recommendations),
            "insights": [
                f"Using {strategy} draft strategy",
                f"Analyzed {len(available_players)} available players",
                "Cross-referenced with Yahoo rankings",
                "Recommendations prioritize available players only",
            ],
        }

    except Exception as e:
        return {
            "status": "error",
            "error": f"Draft recommendation failed: {str(e)}",
            "fallback": "Use ff_get_draft_rankings and ff_get_players for manual analysis",
        }


async def analyze_draft_state_simple(league_key: str, strategy: str) -> dict:
    """Simplified draft state analysis."""
    try:
        # Get current roster and league info
        await yahoo_api_call(f"league/{league_key}/teams")
        leagues = await discover_leagues()
        league_info = leagues.get(league_key, {})

        # Analyze positional needs (simplified)
        user_team = await get_user_team_info(league_key)

        # Get current week to estimate draft progress
        current_week = league_info.get("current_week", 1)
        draft_phase = "pre_season" if current_week <= 1 else "mid_season"

        positional_needs = {
            "QB": "medium",  # Usually need 1-2
            "RB": "high",  # Need 3-5
            "WR": "high",  # Need 3-5
            "TE": "medium",  # Need 1-2
            "K": "low",  # Stream position
            "DEF": "low",  # Stream position
        }

        strategic_advice = []
        if strategy == "conservative":
            strategic_advice.append("Focus on proven players with consistent production")
            strategic_advice.append("Avoid injury-prone or rookie players early")
        elif strategy == "aggressive":
            strategic_advice.append("Target high-upside players and breakout candidates")
            strategic_advice.append("Consider reaching for players with league-winning potential")
        else:
            strategic_advice.append("Balance safety with upside potential")
            strategic_advice.append("Follow tier-based drafting approach")

        return {
            "status": "success",
            "league_key": league_key,
            "strategy": strategy,
            "analysis": {
                "draft_phase": draft_phase,
                "league_info": {
                    "name": league_info.get("name", "Unknown"),
                    "teams": league_info.get("num_teams", 12),
                    "scoring": league_info.get("scoring_type", "standard"),
                },
                "positional_needs": positional_needs,
                "strategic_advice": strategic_advice,
                "your_team": (
                    user_team.get("team_name", "Unknown") if user_team else "Team info unavailable"
                ),
            },
            "recommendations": [
                "Use ff_get_draft_recommendation for specific player suggestions",
                "Monitor ff_get_players for available free agents",
                "Check ff_get_draft_rankings for current ADP data",
            ],
        }

    except Exception as e:
        return {
            "status": "error",
            "error": f"Draft analysis failed: {str(e)}",
            "basic_info": "Use ff_get_league_info for basic league details",
        }


# ==============================================================================
# DEPENDENCY INJECTION - Wire up handler dependencies
# ==============================================================================

# Inject dependencies for league handlers
inject_league_helpers(
    discover_leagues=discover_leagues,
    get_user_team_info=get_user_team_info,
    get_all_teams_info=get_all_teams_info,
)

# Inject dependencies for roster handlers
inject_roster_dependencies(
    get_user_team_info=get_user_team_info,
    yahoo_api_call=yahoo_api_call,
    parse_team_roster=parse_team_roster,
)

# Inject dependencies for matchup handlers
inject_matchup_dependencies(
    get_user_team_key=get_user_team_key,
    get_user_team_info=get_user_team_info,
    yahoo_api_call=yahoo_api_call,
    parse_team_roster=parse_team_roster,
)

# Inject dependencies for player handlers
inject_player_dependencies(
    yahoo_api_call=yahoo_api_call,
    get_waiver_wire_players=get_waiver_wire_players,
)

# Inject dependencies for draft handlers
inject_draft_dependencies(
    get_all_teams_info=get_all_teams_info,
    get_draft_rankings=get_draft_rankings,
    get_draft_recommendation_simple=get_draft_recommendation_simple,
    analyze_draft_state_simple=analyze_draft_state_simple,
    DRAFT_AVAILABLE=DRAFT_AVAILABLE,
)


async def main():
    """Run the MCP server."""
    # Use stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
