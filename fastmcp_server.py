from __future__ import annotations

"""FastMCP-compatible fantasy football server entry point.

This module wraps the existing Yahoo Fantasy Football tooling defined in
``fantasy_football_multi_league`` and exposes it through the FastMCP
``@server.tool`` decorator so it can be deployed on fastmcp.cloud.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Sequence, Union

from fastmcp import Context, FastMCP
from mcp.types import ContentBlock, TextContent

import fantasy_football_multi_league

# REMOVED: enhanced_mcp_tools imports - no longer using wrapper tools

# Remove explicit typing to avoid type conflicts with evolving MCP types
_legacy_call_tool = fantasy_football_multi_league.call_tool
_legacy_refresh_token = fantasy_football_multi_league.refresh_yahoo_token

server = FastMCP(
    name="fantasy-football",
    instructions=(
        "Yahoo Fantasy Football operations including league discovery, roster "
        "analysis, waiver insights, draft tools, and Reddit sentiment checks. "
        "Set the YAHOO_* environment variables before starting the server."
    ),
)

_TOOL_PROMPTS: Dict[str, str] = {
    "ff_get_leagues": (
        "🏈 LEAGUE DISCOVERY - List all Yahoo fantasy leagues for the user. "
        "Takes NO parameters. Use FIRST to get league_key values. "
        "For player searches use ff_get_players or ff_get_waiver_wire."
    ),
    "ff_get_league_info": (
        "📋 Get league configuration and settings. "
        "Parameters: league_key only. Returns scoring type and your team summary."
    ),
    "ff_get_standings": (
        "🏆 Get current league standings. "
        "Parameters: league_key only. Returns ranks, records, points for all teams."
    ),
    "ff_get_roster": (
        "Get roster data with configurable detail levels. Use data_level='basic' for "
        "quick roster info, 'standard' for roster + projections, or 'full' for "
        "comprehensive analysis with external data sources and enhanced insights."
    ),
    "ff_get_matchup": (
        "🆚 Get weekly matchup for your team. "
        "Parameters: league_key (required), week (optional). Returns opponent and projections."
    ),
    "ff_get_players": (
        "Research free agents or player pools for waiver pickups by filtering "
        "Yahoo players by position and limiting the result count. Accepts optional "
        "parameters for enhanced analysis similar to roster data."
    ),
    "ff_compare_teams": (
        "Contrast two league rosters side-by-side to evaluate trades or matchup "
        "advantages. Provide both Yahoo team keys."
    ),
    "ff_build_lineup": (
        "Build optimal lineup from your roster using strategy-based optimization and positional constraints."
    ),
    "ff_refresh_token": (
        "🔑 Refresh Yahoo OAuth token. "
        "NO parameters. Use for expired-token 401s (oauth_problem=token_rejected). "
        "Does NOT help with additional_authorization_required - that means the Yahoo app "
        "lacks Fantasy Sports API provisioning; see INSTALLATION.md."
    ),
    "ff_get_api_status": (
        "📊 Check API health and rate limits. "
        "NO parameters. Returns cache metrics and throttling status."
    ),
    "ff_clear_cache": (
        "Clear cached Yahoo responses to force the next call to fetch fresh "
        "data. Optionally specify a pattern to target certain entries."
    ),
    "ff_get_draft_results": (
        "Retrieve the draft board and pick summaries for every team in a league "
        "after the draft has completed."
    ),
    "ff_get_waiver_wire": (
        "List waiver-wire candidates sorted by rank, points, or trends to aid "
        "mid-season roster moves."
    ),
    "ff_get_draft_rankings": (
        "Access Yahoo pre-draft rankings and ADP information for planning "
        "upcoming drafts, filtered by position if desired."
    ),
    "ff_get_draft_recommendation": (
        "Recommend players to draft at the current or upcoming pick based on "
        "your strategy and league context."
    ),
    "ff_analyze_draft_state": (
        "Evaluate the evolving draft board for your team to highlight "
        "positional needs and strategy adjustments."
    ),
    "ff_analyze_reddit_sentiment": (
        "Summarize recent Reddit sentiment and engagement around one or more "
        "players to complement scouting insights."
    ),
}


def _tool_meta(name: str) -> Dict[str, str]:
    """Helper to attach consistent prompt metadata to each tool."""

    return {"prompt": _TOOL_PROMPTS[name]}


async def _call_legacy_tool(
    name: str,
    *,
    ctx: Context | None = None,
    **arguments: Any,
) -> Dict[str, Any]:
    """Delegate to the legacy MCP tool implementation and parse its JSON payload."""

    filtered_args = {key: value for key, value in arguments.items() if value is not None}

    if ctx is not None:
        await ctx.info(f"Calling legacy Yahoo tool: {name}")

    raw_blocks = await _legacy_call_tool(name=name, arguments=filtered_args)
    if raw_blocks is None:
        blocks: Sequence[Any] = []
    elif isinstance(raw_blocks, Iterable) and not isinstance(raw_blocks, (str, bytes, TextContent)):
        blocks = list(raw_blocks)
    else:
        blocks = [raw_blocks]
    if not blocks:
        return {
            "status": "error",
            "message": "Legacy tool returned no response",
            "tool": name,
            "arguments": filtered_args,
        }

    def _coerce_text(block: Any) -> TextContent:
        if isinstance(block, TextContent):
            return block
        if hasattr(block, "text") and isinstance(getattr(block, "text"), str):
            return TextContent(type="text", text=getattr(block, "text"))
        if is_dataclass(block) and not isinstance(block, type):
            return TextContent(type="text", text=json.dumps(asdict(block)))
        if hasattr(block, "data"):
            data = getattr(block, "data")
            if isinstance(data, bytes):
                try:
                    data = data.decode("utf-8")
                except Exception:
                    data = repr(data)
            if isinstance(data, str):
                return TextContent(type="text", text=data)
        try:
            return TextContent(type="text", text=json.dumps(block, default=str))
        except Exception:
            return TextContent(type="text", text=str(block))

    responses = [_coerce_text(block) for block in blocks]

    first = responses[0]
    payload = getattr(first, "text", "")

    # Instrumentation: detect raw '0' / suspiciously tiny payloads that break higher layers
    if payload.strip() == "0":
        diag = {
            "status": "error",
            "message": "Legacy tool returned sentinel '0' string instead of JSON",
            "tool": name,
            "arguments": filtered_args,
            "raw": payload,
            "stage": "_call_legacy_tool:raw_payload_zero",
        }
        if ctx is not None:
            await ctx.info(f"[diagnostic] Detected raw '0' payload from legacy tool: {name}")
        return diag

    if not payload:
        return {
            "status": "error",
            "message": "Legacy tool returned an empty payload",
            "tool": name,
            "arguments": filtered_args,
        }

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Could not parse legacy response as JSON",
            "tool": name,
            "arguments": filtered_args,
            "raw": payload,
        }


@server.tool(
    name="ff_get_leagues",
    description=(
        "🏈 LEAGUE DISCOVERY - Get list of your Yahoo fantasy leagues. "
        "NO parameters required (no position/count/sort). "
        "Use this FIRST to get league_key values. "
        "For player searches use ff_get_players or ff_get_waiver_wire."
    ),
    meta=_tool_meta("ff_get_leagues"),
)
async def ff_get_leagues(ctx: Context) -> Dict[str, Any]:
    """
    Discover all Yahoo fantasy football leagues for the authenticated user.

    ⚠️ IMPORTANT: This tool takes NO search parameters!
    - NO position, count, sort, week, or team_key parameters
    - This is for LEAGUE DISCOVERY only, not player searches

    For player searches use:
    - ff_get_players → Search available players by position
    - ff_get_waiver_wire → Waiver wire analysis with rankings
    - ff_get_roster → Get YOUR team's current roster

    Returns:
        Dict with total_leagues count and list of league summaries
    """
    return await _call_legacy_tool("ff_get_leagues", ctx=ctx)


@server.tool(
    name="ff_get_league_info",
    description=(
        "📋 Get league configuration and settings. "
        "Parameters: league_key (required only). "
        "Returns scoring type, roster requirements, and your team summary."
    ),
    meta=_tool_meta("ff_get_league_info"),
)
async def ff_get_league_info(
    ctx: Context,
    league_key: str,
) -> Dict[str, Any]:
    """
    Retrieve metadata about a single Yahoo league.

    Args:
        league_key: League identifier (required)

    Returns:
        Dict with league settings, scoring type, and your team info
    """
    return await _call_legacy_tool(
        "ff_get_league_info",
        ctx=ctx,
        league_key=league_key,
    )


@server.tool(
    name="ff_get_roster",
    description=(
        "⚠️ Get YOUR TEAM'S current roster (YOUR players only). "
        "DO NOT use this to search for available players! "
        "Parameters: league_key, team_key, week, data_level, include_projections, include_external_data, include_analysis. "
        "For available players use ff_get_players or ff_get_waiver_wire."
    ),
    meta=_tool_meta("ff_get_roster"),
)
async def ff_get_roster(
    ctx: Context,
    league_key: str,
    team_key: Optional[str] = None,
    week: Optional[int] = None,
    include_projections: bool = True,
    include_external_data: bool = True,
    include_analysis: bool = True,
    data_level: Optional[Literal["basic", "standard", "full"]] = None,
) -> Dict[str, Any]:
    """
    Get YOUR TEAM'S roster with configurable detail levels.

    ⚠️ IMPORTANT: This tool ONLY gets YOUR roster, not available players.
    - To search available players by position → use ff_get_players
    - For waiver wire pickups with rankings → use ff_get_waiver_wire

    This tool does NOT accept: position, count, sort, include_expert_analysis

    Args:
        league_key: League identifier
        team_key: Team identifier (optional, defaults to authenticated user's team)
        week: Week number for projections (optional, defaults to current week)
        include_projections: Include Yahoo and/or Sleeper projections
        include_external_data: Include Sleeper rankings, matchup analysis, trending data
        include_analysis: Include enhanced player analysis and recommendations
        data_level: "basic" (roster only), "standard" (+ projections), "full" (everything)
    """

    # Ensure we have a valid data_level
    if data_level is None:
        data_level = "full"

    # Determine effective settings based on data_level and explicit parameters
    if data_level == "basic":
        effective_projections = False
        effective_external = False
        effective_analysis = False
    elif data_level == "standard":
        effective_projections = True
        effective_external = False
        effective_analysis = False
    else:  # "full"
        effective_projections = True
        effective_external = True
        effective_analysis = True

    # Explicit parameters override data_level defaults
    if not include_projections:
        effective_projections = False
    if not include_external_data:
        effective_external = False
    if not include_analysis:
        effective_analysis = False

    # Informational logging for the selected mode
    if ctx:
        if not any([effective_projections, effective_external, effective_analysis]):
            await ctx.info("Using basic roster data (legacy mode)")
        else:
            await ctx.info(
                "Using enhanced roster data "
                f"(projections: {effective_projections}, external: {effective_external}, analysis: {effective_analysis})"
            )

    try:
        result = await _call_legacy_tool(
            "ff_get_roster",
            ctx=ctx,
            league_key=league_key,
            team_key=team_key,
            week=week,
            include_projections=effective_projections,
            include_external_data=effective_external,
            include_analysis=effective_analysis,
            data_level=data_level,
        )
        return result
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Enhanced roster fetch failed: {exc}",
            "fallback_suggestion": "Try using data_level='basic' for simple roster data",
        }


@server.tool(
    name="ff_get_standings",
    description=(
        "🏆 Get current league standings and team records. "
        "Parameters: league_key (required only). "
        "Returns rank, wins, losses, points for/against for all teams."
    ),
    meta=_tool_meta("ff_get_standings"),
)
async def ff_get_standings(
    ctx: Context,
    league_key: str,
) -> Dict[str, Any]:
    """
    Return the current standings table for a Yahoo league.

    Args:
        league_key: League identifier (required)

    Returns:
        Dict with sorted standings showing ranks, records, and points
    """
    return await _call_legacy_tool("ff_get_standings", ctx=ctx, league_key=league_key)


@server.tool(
    name="ff_get_matchup",
    description=(
        "🆚 Get weekly matchup for your team. "
        "Parameters: league_key (required), week (optional, defaults to current). "
        "Returns opponent info and projected scores."
    ),
    meta=_tool_meta("ff_get_matchup"),
)
async def ff_get_matchup(
    ctx: Context,
    league_key: str,
    week: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Retrieve matchup information for the authenticated team.

    Args:
        league_key: League identifier (required)
        week: Week number (optional, defaults to current week)

    Returns:
        Dict with matchup data including opponent and projections
    """
    return await _call_legacy_tool(
        "ff_get_matchup",
        ctx=ctx,
        league_key=league_key,
        week=week,
    )


@server.tool(
    name="ff_get_players",
    description=(
        "🔍 Search AVAILABLE players by position with count limit. "
        "Use this to find free agents by position (QB, RB, WR, TE). "
        "Parameters: league_key, position, count, week. "
        "For YOUR roster use ff_get_roster. For waiver analysis use ff_get_waiver_wire."
    ),
    meta=_tool_meta("ff_get_players"),
)
async def ff_get_players(
    ctx: Context,
    league_key: str,
    position: Optional[str] = None,
    count: int = 10,
    week: Optional[int] = None,
    team_key: Optional[str] = None,
    data_level: Optional[Literal["basic", "standard", "full"]] = None,
    include_analysis: Optional[bool] = None,
    include_projections: Optional[bool] = None,
    include_external_data: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Enhanced player search with expert analysis and Sleeper integration.

    Args:
        league_key: League identifier
        position: Filter by position (QB, RB, WR, TE, etc.)
        count: Number of players to return
        week: Week for analysis context
        data_level: "basic" (names only), "standard" (+ stats), "full" (+ expert analysis)
        include_analysis: Include expert tiers and recommendations
        include_projections: Include projection data
        include_external_data: Include Sleeper rankings and trending data
    """

    # Default to enhanced mode for better player analysis
    if data_level is None:
        data_level = "full"
    if include_analysis is None:
        include_analysis = True
    if include_external_data is None:
        include_external_data = True

    return await _call_legacy_tool(
        "ff_get_players",
        ctx=ctx,
        league_key=league_key,
        position=position,
        count=count,
        week=week,
        team_key=team_key,
        data_level=data_level,
        include_analysis=include_analysis,
        include_projections=include_projections,
        include_external_data=include_external_data,
    )


@server.tool(
    name="ff_compare_teams",
    description=(
        "Compare the rosters of two teams in the same league to support trade "
        "or matchup analysis. Provide both team keys."
    ),
    meta=_tool_meta("ff_compare_teams"),
)
async def ff_compare_teams(
    ctx: Context,
    league_key: str,
    team_key_a: str,
    team_key_b: str,
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_compare_teams",
        ctx=ctx,
        league_key=league_key,
        team_key_a=team_key_a,
        team_key_b=team_key_b,
    )


@server.tool(
    name="ff_build_lineup",
    description=(
        "Build optimal lineup from your roster using strategy-based optimization and positional constraints. "
        "Uses advanced analytics including matchup analysis, player projections, and situational factors."
    ),
    meta=_tool_meta("ff_build_lineup"),
)
async def ff_build_lineup(
    ctx: Context,
    league_key: str,
    week: Optional[int] = None,
    strategy: Literal["conservative", "aggressive", "balanced"] = "balanced",
    debug: bool = False,
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_build_lineup",
        ctx=ctx,
        league_key=league_key,
        week=week,
        strategy=strategy,
        debug=debug,
    )


@server.tool(
    name="ff_refresh_token",
    description=(
        "🔑 Refresh Yahoo OAuth token. "
        "NO parameters required. "
        "Use when API calls return 401 errors from an expired token "
        "(oauth_problem=token_rejected). A 401 with "
        "additional_authorization_required is a provisioning problem the "
        "refresh cannot fix - the Yahoo app needs Fantasy Sports API access "
        "approved at https://sports.yahoo.com/developer/access/."
    ),
    meta=_tool_meta("ff_refresh_token"),
)
async def ff_refresh_token(ctx: Context) -> Dict[str, Any]:
    """
    Refresh the Yahoo OAuth access token.

    ⚠️ Takes NO parameters - automatic token refresh only

    Returns:
        Dict with token refresh status
    """
    if ctx is not None:
        await ctx.info("Refreshing Yahoo OAuth token")
    return await _legacy_refresh_token()


@server.tool(
    name="ff_get_api_status",
    description=(
        "📊 Check API health and rate limits. "
        "NO parameters required. "
        "Returns cache metrics and API throttling status."
    ),
    meta=_tool_meta("ff_get_api_status"),
)
async def ff_get_api_status(ctx: Context) -> Dict[str, Any]:
    """
    Inspect rate limiter and cache metrics for troubleshooting.

    ⚠️ Takes NO parameters - system diagnostic tool only

    Returns:
        Dict with API status, rate limits, and cache metrics
    """
    return await _call_legacy_tool("ff_get_api_status", ctx=ctx)


@server.tool(
    name="ff_clear_cache",
    description=(
        "Invalidate the Yahoo response cache. Optionally provide a pattern to "
        "clear a subset of cached endpoints."
    ),
    meta=_tool_meta("ff_clear_cache"),
)
async def ff_clear_cache(
    ctx: Context,
    pattern: Optional[str] = None,
) -> Dict[str, Any]:
    return await _call_legacy_tool("ff_clear_cache", ctx=ctx, pattern=pattern)


@server.tool(
    name="ff_get_draft_results",
    description=(
        "Fetch draft grades and pick positions for every team in a league to "
        "review draft performance."
    ),
    meta=_tool_meta("ff_get_draft_results"),
)
async def ff_get_draft_results(ctx: Context, league_key: str) -> Dict[str, Any]:
    return await _call_legacy_tool("ff_get_draft_results", ctx=ctx, league_key=league_key)


@server.tool(
    name="ff_get_waiver_wire",
    description=(
        "📊 Get waiver wire pickups with RANKINGS, SORTING, and expert analysis. "
        "Use this for waiver priority decisions with sort options (rank/points/owned/trending). "
        "Parameters: league_key, position, sort, count, include_expert_analysis. "
        "For YOUR roster use ff_get_roster. For simple player search use ff_get_players."
    ),
    meta=_tool_meta("ff_get_waiver_wire"),
)
async def ff_get_waiver_wire(
    ctx: Context,
    league_key: str,
    position: Optional[str] = None,
    sort: Literal["rank", "points", "owned", "trending"] = "rank",
    count: int = 30,
    week: Optional[int] = None,
    team_key: Optional[str] = None,
    include_expert_analysis: bool = True,
    data_level: Optional[Literal["basic", "standard", "full"]] = None,
) -> Dict[str, Any]:
    """
    Enhanced waiver wire analysis with expert recommendations.

    Args:
        league_key: League identifier
        position: Filter by position (QB, RB, WR, TE, etc.) - defaults to "all"
        sort: Sort method - "rank" (expert), "points" (season), "owned" (popularity), "trending" (hot pickups)
        count: Number of players to return
        week: Week for projections (optional, defaults to current)
        team_key: Team key for context (optional)
        include_expert_analysis: Include tiers, recommendations, and confidence scores
        data_level: Data detail level ("basic", "standard", "full")
    """

    # Default to enhanced mode for better waiver analysis, but basic mode if expert analysis disabled
    if data_level is None:
        data_level = "full" if include_expert_analysis else "basic"

    # Handle position default - convert None to "all"
    if position is None:
        position = "all"

    try:
        # Map data_level to legacy parameters for backward compatibility
        if data_level == "basic":
            include_projections = False
            include_external_data = False
            include_analysis = False
        elif data_level == "standard":
            include_projections = True
            include_external_data = False
            include_analysis = False
        else:  # "full"
            include_projections = True
            include_external_data = True
            include_analysis = include_expert_analysis

        result = await _call_legacy_tool(
            "ff_get_waiver_wire",
            ctx=ctx,
            league_key=league_key,
            position=position,
            sort=sort,
            count=count,
            week=week,
            team_key=team_key,
            include_projections=include_projections,
            include_external_data=include_external_data,
            include_analysis=include_analysis,
        )

        # Check if main server provided enhanced players
        if include_expert_analysis and result.get("enhanced_players"):
            # Main server handled the enhancement - use enhanced data
            if ctx:
                await ctx.info("Using enhanced waiver wire data from main server...")

            # Replace basic players with enhanced players for better data
            result["players"] = result["enhanced_players"]

            # Ensure proper sorting based on request
            if sort == "rank" and result["players"]:
                # Sort by waiver_priority if available, else expert_confidence
                if "waiver_priority" in result["players"][0]:
                    result["players"].sort(key=lambda x: x.get("waiver_priority", 0), reverse=True)
                else:
                    result["players"].sort(
                        key=lambda x: x.get("expert_confidence", 0), reverse=True
                    )
            elif sort == "trending":
                result["players"].sort(key=lambda x: x.get("trending_score", 50), reverse=True)
        elif include_expert_analysis and ctx:
            await ctx.info("Expert analysis requested but not available from main server")

        return result

    except Exception as exc:
        return {
            "status": "error",
            "message": f"Waiver wire analysis failed: {exc}",
            "league_key": league_key,
        }


@server.tool(
    name="ff_get_draft_rankings",
    description=(
        "Access pre-draft Yahoo rankings and ADP data. Useful before or during "
        "drafts to evaluate player tiers."
    ),
    meta=_tool_meta("ff_get_draft_rankings"),
)
async def ff_get_draft_rankings(
    ctx: Context,
    league_key: Optional[str] = None,
    position: Optional[str] = "all",
    count: int = 50,
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_get_draft_rankings",
        ctx=ctx,
        league_key=league_key,
        position=position,
        count=count,
    )


@server.tool(
    name="ff_get_draft_recommendation",
    description=(
        "Provide draft pick recommendations tailored to a strategy such as "
        "balanced, aggressive, or conservative."
    ),
    meta=_tool_meta("ff_get_draft_recommendation"),
)
async def ff_get_draft_recommendation(
    ctx: Context,
    league_key: str,
    strategy: Literal["conservative", "aggressive", "balanced"] = "balanced",
    num_recommendations: int = 10,
    current_pick: Optional[int] = None,
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_get_draft_recommendation",
        ctx=ctx,
        league_key=league_key,
        strategy=strategy,
        num_recommendations=num_recommendations,
        current_pick=current_pick,
    )


@server.tool(
    name="ff_analyze_draft_state",
    description=(
        "Summarize the current draft landscape for your team, highlighting "
        "positional needs and strategic advice."
    ),
    meta=_tool_meta("ff_analyze_draft_state"),
)
async def ff_analyze_draft_state(
    ctx: Context,
    league_key: str,
    strategy: Literal["conservative", "aggressive", "balanced"] = "balanced",
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_analyze_draft_state",
        ctx=ctx,
        league_key=league_key,
        strategy=strategy,
    )


@server.tool(
    name="ff_analyze_reddit_sentiment",
    description=(
        "Analyze recent Reddit chatter for one or more players to gauge public "
        "sentiment, injury mentions, and engagement levels."
    ),
    meta=_tool_meta("ff_analyze_reddit_sentiment"),
)
async def ff_analyze_reddit_sentiment(
    ctx: Context,
    players: Sequence[str],
    time_window_hours: int = 48,
) -> Dict[str, Any]:
    return await _call_legacy_tool(
        "ff_analyze_reddit_sentiment",
        ctx=ctx,
        players=list(players),
        time_window_hours=time_window_hours,
    )


# ============================================================================
# ENHANCED TOOLS - Advanced decision-making capabilities for client LLMs
# ============================================================================

# REMOVED: ff_get_roster_with_projections_wrapper - replaced by ff_get_roster with data_level='full'
# REMOVED: ff_analyze_lineup_options_wrapper - complex functionality can be achieved through ff_build_lineup


# REMOVED: ff_compare_players_wrapper - player comparison can be done through ff_get_players and ff_get_waiver_wire


# REMOVED: ff_what_if_analysis_wrapper - scenario analysis can be done using ff_build_lineup with different strategies


# REMOVED: ff_get_decision_context_wrapper - context can be gathered through ff_get_league_info, ff_get_matchup, ff_get_standings


# ============================================================================
# PROMPTS - Reusable message templates for better LLM interactions
# ============================================================================


@server.prompt
def analyze_roster_strengths(league_key: str, team_key: str) -> str:
    """Generate a prompt for analyzing roster strengths and weaknesses."""
    return f"""Please analyze the fantasy football roster for team {team_key} in league {league_key}. 
    
Focus on:
1. Positional depth and strength
2. Starting lineup quality vs bench depth
3. Injury concerns and bye week coverage
4. Trade opportunities and waiver wire needs
5. Overall team competitiveness

Provide specific recommendations for improvement."""


@server.prompt
def draft_strategy_advice(strategy: str, league_size: int, pick_position: int) -> str:
    """Generate a prompt for draft strategy recommendations."""
    return f"""Provide fantasy football draft strategy advice for:
- Strategy: {strategy}
- League size: {league_size} teams
- Draft position: {pick_position}

Include:
1. First 3 rounds strategy
2. Position priority order
3. Sleepers and value picks
4. Players to avoid
5. Late-round targets
6. PPR-specific considerations (pass-catching RBs, high-volume WRs)

Tailor the advice to the {strategy} approach and consider how PPR scoring affects player values."""


@server.prompt
def matchup_analysis(team_a: str, team_b: str, week: int) -> str:
    """Generate a prompt for head-to-head matchup analysis."""
    return f"""Analyze the fantasy football matchup between {team_a} and {team_b} for Week {week}.

Compare:
1. Starting lineup projections
2. Key positional advantages
3. Weather/venue factors
4. Recent performance trends
5. Injury reports and player status
6. Predicted outcome and confidence level

Provide a detailed breakdown with specific player recommendations."""


@server.prompt
def waiver_wire_priority(league_key: str, position: str, budget: int) -> str:
    """Generate a prompt for waiver wire priority recommendations."""
    return f"""Analyze waiver wire options for {position} in league {league_key} with a budget of ${budget}.

Evaluate:
1. Top 5 available players at {position}
2. FAAB bid recommendations
3. Long-term vs short-term value
4. Injury replacements vs upgrades
5. Schedule analysis for upcoming weeks

Prioritize based on immediate need and future potential."""


@server.prompt
def trade_evaluation(team_a: str, team_b: str, proposed_trade: str) -> str:
    """Generate a prompt for trade evaluation."""
    return f"""Evaluate this fantasy football trade proposal between {team_a} and {team_b}:

Proposed Trade: {proposed_trade}

Analyze:
1. Fairness and value balance
2. Team needs and fit
3. Positional scarcity impact
4. Playoff schedule implications
5. Risk vs reward assessment
6. Alternative trade suggestions

Provide a clear recommendation with reasoning."""


@server.prompt
def start_sit_decision(league_key: str, position: str, player_names: list[str], week: int) -> str:
    """Generate a prompt for start/sit decision making."""
    players_str = ", ".join(player_names)
    return f"""Help me decide who to START at {position} for Week {week} in league {league_key}.

Players to consider: {players_str}

Analyze:
1. Projected points and ceiling/floor
2. Matchup quality and defensive rankings
3. Recent performance trends (last 3 weeks)
4. Injury concerns and game status
5. Weather and game environment factors
6. Target share / snap count / usage trends
7. Game script prediction (positive/negative)

Provide a clear START/SIT recommendation with confidence level and reasoning."""


@server.prompt
def bye_week_planning(league_key: str, team_key: str, upcoming_weeks: int) -> str:
    """Generate a prompt for bye week planning and roster management."""
    return f"""Plan for upcoming bye weeks for team {team_key} in league {league_key} over the next {upcoming_weeks} weeks.

Analyze:
1. Which starters have byes in each week
2. Current bench depth at affected positions
3. Waiver wire options to cover gaps
4. Potential streaming candidates
5. Drop candidates to make room
6. Multi-week planning strategy

Provide a week-by-week action plan."""


@server.prompt  
def playoff_preparation(league_key: str, team_key: str, current_week: int) -> str:
    """Generate a prompt for playoff preparation strategy."""
    return f"""Create a playoff preparation strategy for team {team_key} in league {league_key} (currently Week {current_week}).

Focus on:
1. Playoff schedule strength analysis (Weeks 15-17)
2. Key players to acquire before deadline
3. Handcuffs and insurance plays
4. Bench streamlining for playoff roster
5. Injury risk assessment for key players
6. Championship-winning moves to make now
7. Weather considerations for late season

Provide actionable recommendations to maximize playoff success."""


@server.prompt
def trade_proposal_generation(league_key: str, my_team_key: str, target_team_key: str, position_need: str) -> str:
    """Generate a prompt for creating fair trade proposals."""
    return f"""Generate fair trade proposals between my team ({my_team_key}) and {target_team_key} in league {league_key}.

My need: {position_need}

Create proposals that:
1. Address my positional need
2. Fill a gap for the other team
3. Are fair value for both sides
4. Consider team contexts and records
5. Account for bye weeks and playoffs
6. Include 2-3 different trade options

For each proposal explain why it works for both teams."""


@server.prompt
def injury_replacement_strategy(league_key: str, injured_player: str, injury_length: str, position: str) -> str:
    """Generate a prompt for injury replacement analysis."""
    return f"""My player {injured_player} ({position}) is injured for approximately {injury_length} in league {league_key}.

Develop a replacement strategy:
1. Short-term vs long-term replacement approach
2. Top 5 waiver wire targets with analysis
3. Trade targets if waiver wire is thin
4. FAAB bidding strategy (if applicable)
5. Handcuff analysis for the injured player's backup
6. Roster moves needed (drops to consider)
7. Timeline for return and stash strategy

Provide immediate action items and contingency plans."""


@server.prompt
def streaming_dst_kicker(league_key: str, week: int, position: str) -> str:
    """Generate a prompt for streaming defense or kicker recommendations."""
    pos_full = "Defense/Special Teams" if position == "DEF" else "Kicker"
    return f"""Recommend {pos_full} streaming options for Week {week} in league {league_key}.

Analyze:
1. Top 5 available {pos_full} options this week
2. Matchup analysis and opponent rankings
3. Vegas lines and game environment
4. Weather factors (if relevant)
5. Next 2-3 weeks schedule preview
6. Season-long hold vs weekly stream
7. Ownership percentage and availability

Rank options with confidence levels and reasoning."""


@server.prompt
def season_long_strategy_check(league_key: str, team_key: str, current_record: str, weeks_remaining: int) -> str:
    """Generate a prompt for comprehensive season strategy assessment."""
    return f"""Assess season-long strategy for team {team_key} in league {league_key}.

Current record: {current_record}
Weeks remaining: {weeks_remaining}

Comprehensive analysis:
1. Playoff probability and path
2. Win-now vs build-for-future approach
3. Trade deadline strategy (aggressive/hold/sell)
4. Waiver wire priority adjustments
5. Key matchups and must-win games
6. Positional advantages vs league
7. Risk tolerance recommendations

Provide strategic guidance for rest of season."""


@server.prompt
def weekly_game_plan(league_key: str, team_key: str, opponent_team_key: str, week: int) -> str:
    """Generate a comprehensive weekly game plan prompt."""
    return f"""Create a complete game plan for Week {week} matchup between {team_key} and {opponent_team_key} in league {league_key}.

Develop strategy covering:
1. Optimal starting lineup with justification
2. Start/sit decisions with reasoning
3. Opponent's likely lineup and key players
4. Positional advantages to exploit
5. Risk assessment (safe plays vs boom/bust)
6. Weather and game environment factors
7. Waiver claims needed before games
8. Expected score and win probability

Provide a complete action plan for maximum points."""


# ============================================================================
# RESOURCES - Static and dynamic data for LLM context
# ============================================================================


@server.resource("config://scoring")
def get_scoring_rules() -> str:
    """Provide standard fantasy football scoring rules for context."""
    return """Fantasy Football Scoring Rules:

PASSING:
- Passing TD: 4 points
- Passing Yards: 1 point per 25 yards
- Interception: -2 points
- 2-Point Conversion: 2 points

RUSHING:
- Rushing TD: 6 points
- Rushing Yards: 1 point per 10 yards
- 2-Point Conversion: 2 points

RECEIVING:
- Receiving TD: 6 points
- Receiving Yards: 1 point per 10 yards
- Reception: 1 point (PPR - Points Per Reception)
- 2-Point Conversion: 2 points

KICKING:
- Field Goal 0-39 yards: 3 points
- Field Goal 40-49 yards: 4 points
- Field Goal 50+ yards: 5 points
- Extra Point: 1 point

DEFENSE/SPECIAL TEAMS:
- Touchdown: 6 points
- Safety: 2 points
- Interception: 2 points
- Fumble Recovery: 2 points
- Sack: 1 point
- Blocked Kick: 2 points
- Points Allowed 0: 10 points
- Points Allowed 1-6: 7 points
- Points Allowed 7-13: 4 points
- Points Allowed 14-20: 1 point
- Points Allowed 21-27: 0 points
- Points Allowed 28-34: -1 point
- Points Allowed 35+: -4 points

SCORING VARIATIONS:
- Standard (Non-PPR): 0 points per reception
- Half-PPR: 0.5 points per reception
- Full-PPR: 1 point per reception (most common)
- Super-PPR: 1.5+ points per reception

PPR IMPACT:
- Increases value of pass-catching RBs and slot WRs
- Makes WRs more valuable relative to RBs
- Favors high-volume receivers over big-play specialists
- Changes draft strategy and player rankings"""


@server.resource("config://positions")
def get_position_info() -> str:
    """Provide fantasy football position information and requirements."""
    return """Fantasy Football Position Requirements:

STANDARD LEAGUE (10-12 teams):
- QB: 1 starter
- RB: 2 starters
- WR: 2 starters  
- TE: 1 starter
- FLEX: 1 (RB/WR/TE)
- K: 1 starter
- DEF/ST: 1 starter
- Bench: 6-7 players

SUPERFLEX LEAGUE:
- QB: 1 starter
- RB: 2 starters
- WR: 2 starters
- TE: 1 starter
- FLEX: 1 (RB/WR/TE)
- SUPERFLEX: 1 (QB/RB/WR/TE)
- K: 1 starter
- DEF/ST: 1 starter
- Bench: 6-7 players

POSITION ABBREVIATIONS:
- QB: Quarterback
- RB: Running Back
- WR: Wide Receiver
- TE: Tight End
- K: Kicker
- DEF/ST: Defense/Special Teams
- FLEX: Flexible position (RB/WR/TE)
- SUPERFLEX: Super flexible position (QB/RB/WR/TE)"""


@server.resource("config://strategies")
def get_draft_strategies() -> str:
    """Provide information about different fantasy football draft strategies."""
    return """Fantasy Football Draft Strategies:

CONSERVATIVE STRATEGY:
- Focus on safe, high-floor players
- Prioritize proven veterans
- Avoid injury-prone players
- Build depth over upside
- Target consistent performers
- Good for beginners

BALANCED STRATEGY:
- Mix of safe picks and upside plays
- Balance risk and reward
- Target value at each pick
- Consider positional scarcity
- Adapt to draft flow
- Most popular approach

AGGRESSIVE STRATEGY:
- Target high-upside players
- Take calculated risks
- Focus on ceiling over floor
- Target breakout candidates
- Embrace volatility
- High risk, high reward

POSITIONAL STRATEGIES:
- Zero RB: Wait on running backs (more viable in PPR)
- Hero RB: Draft one elite RB early
- Robust RB: Load up on running backs
- Late Round QB: Wait on quarterback
- Streaming: Target favorable matchups

PPR-SPECIFIC STRATEGIES:
- Target pass-catching RBs (higher floor in PPR)
- Prioritize high-volume WRs over big-play specialists
- Consider slot receivers and possession WRs
- Elite TEs become more valuable (reception floor)
- RB handcuffs less critical (more WR depth)

KEY PRINCIPLES:
- Value-based drafting
- Positional scarcity awareness
- Handcuff important players
- Monitor bye weeks
- Stay flexible and adapt
- PPR changes player values significantly"""


@server.resource("data://injury-status")
def get_injury_status_info() -> str:
    """Provide information about fantasy football injury statuses."""
    return """Fantasy Football Injury Status Guide:

QUESTIONABLE (Q):
- 50% chance to play
- Monitor closely
- Have backup ready
- Check game-time decisions

DOUBTFUL (D):
- 25% chance to play
- Likely to sit out
- Start backup if available
- High risk to start

OUT (O):
- Will not play
- Do not start
- Use backup or waiver pickup
- Check IR eligibility

PROBABLE (P):
- 75% chance to play
- Likely to start
- Monitor for changes
- Generally safe to start

INJURED RESERVE (IR):
- Out for extended time
- Can be stashed in IR slot
- Check league rules
- Monitor return timeline

COVID-19:
- Follow league protocols
- Check testing status
- Monitor updates
- Have backup plans

INACTIVE:
- Will not play
- Game-day decision
- Use alternative options
- Check pre-game reports"""


@server.resource("guide://weekly-strategy")
def get_weekly_strategy_guide() -> str:
    """Provide week-by-week fantasy football strategic guidance."""
    return """Fantasy Football Weekly Strategy Guide:

WEEKS 1-4 (EARLY SEASON):
- Trust preseason rankings and projections
- Don't overreact to single-game performances
- Monitor snap counts and target shares
- Identify emerging trends early
- Stock up on high-upside bench stashes
- Be aggressive on waiver wire for breakouts
- Avoid panic trades after Week 1

WEEKS 5-8 (MID-SEASON):
- Sample size now meaningful for trends
- Target buy-low candidates after slow starts
- Sell high on overperformers
- Plan ahead for bye week hell
- Consolidate depth via 2-for-1 trades
- Stream defenses based on matchups
- Monitor injury reports closely

WEEKS 9-12 (PLAYOFF PUSH):
- Focus on playoff schedule (Weeks 15-17)
- Trade deadline strategy crucial
- Handcuff your stud RBs
- Drop low-floor bench players
- Target players returning from injury
- Win-now moves for playoff teams
- Sell future value if competing

WEEKS 13-14 (PLAYOFF PREP):
- Lock in your playoff roster
- Drop underperformers without hesitation
- Stream defenses for playoff weeks
- Stash handcuffs for key players
- Monitor weather for late season games
- Rest concerns for locked playoff teams
- Final waiver wire pickups

WEEKS 15-17 (PLAYOFFS):
- Championship mentality
- Weather is critical factor
- Monitor resting starters in Week 17
- Have backup plans for all positions
- Trust your studs in playoffs
- Avoid cute plays and overthinking
- Weather-proof your lineup if possible

KEY WEEKLY TASKS:
1. Check injury reports (Wed/Thu/Fri)
2. Review snap counts and usage from prior week
3. Analyze upcoming matchups
4. Submit waiver claims (Tuesday/Wednesday)
5. Check starting lineup before games
6. Monitor weather reports (Saturday/Sunday)
7. Set backup plans for questionable players"""


@server.resource("guide://common-mistakes")
def get_common_mistakes_guide() -> str:
    """Provide guidance on common fantasy football mistakes to avoid."""
    return """Common Fantasy Football Mistakes to Avoid:

DRAFT MISTAKES:
❌ Drafting based on team loyalty
❌ Ignoring bye weeks completely
❌ Reaching for your favorite players
❌ Not adjusting to league scoring
❌ Following outdated rankings
❌ Drafting kicker/defense too early
❌ Ignoring injury history
✅ Value-based drafting with flexibility
✅ Balance safety and upside
✅ Adjust for PPR vs Standard scoring

IN-SEASON MISTAKES:
❌ Overreacting to one bad game
❌ Starting players on bye week
❌ Ignoring weather conditions
❌ Holding too many QBs/TEs/Defenses
❌ Not using all roster spots
❌ Forgetting to set lineup
❌ Trading based on emotion
✅ Use data and trends for decisions
✅ Stay active on waiver wire
✅ Make roster moves every week

WAIVER WIRE MISTAKES:
❌ Burning #1 priority too early
❌ Missing Wednesday waivers
❌ Not checking injury reports
❌ Chasing last week's points
❌ Ignoring opportunity (volume > talent early)
❌ Dropping players after one bad game
✅ Target volume and opportunity
✅ Plan ahead for bye weeks
✅ Be patient with waiver priority

TRADE MISTAKES:
❌ Accepting first offer received
❌ Trading based on name value only
❌ Ignoring team context and situation
❌ Not considering playoff schedule
❌ Vetoing trades out of spite
❌ Trading away depth before bye weeks
❌ Panicking after injuries
✅ Always counter-offer first
✅ Consider both teams' needs
✅ Look at rest-of-season schedules

LINEUP MISTAKES:
❌ Benching studs after bad game
❌ Starting players on snap count
❌ Overthinking Thursday night games
❌ Not checking start times
❌ Ignoring weather reports
❌ Starting questionable players without backup
❌ Getting too cute with lineup
✅ Start your studs
✅ Have contingency plans
✅ Trust projections over gut

STRATEGIC MISTAKES:
❌ Playing for second place
❌ Not taking calculated risks
❌ Holding players for trade value
❌ Ignoring playoff implications
❌ Not handcuffing elite RBs
❌ Hoarding too many bench RBs
✅ Championship-or-bust mentality
✅ Maximize every roster spot
✅ Make bold moves when necessary"""


@server.resource("guide://advanced-stats")
def get_advanced_stats_glossary() -> str:
    """Provide glossary of advanced fantasy football statistics."""
    return """Advanced Fantasy Football Statistics Glossary:

VOLUME METRICS:
- Snap Count %: Percentage of offensive snaps played
  → 70%+ is ideal for RB/WR, 90%+ for elite
- Target Share: Percentage of team targets received
  → 20%+ is WR1 territory, 25%+ is elite
- Touch Count: Total rushing attempts + receptions
  → 15+ touches for RB1, 20+ is workhorse territory
- Red Zone Touches: Carries/targets inside opponent 20
  → High correlation with TDs and fantasy points
- Air Yards: Total depth of targets (catchable or not)
  → Higher air yards = more big play potential

EFFICIENCY METRICS:
- Yards Per Route Run (YPRR): Receiving yards per route
  → 2.0+ is excellent, 2.5+ is elite
- Yards After Contact (YAC): Rushing/receiving yards after contact
  → Indicates home run ability and toughness
- Yards Per Carry (YPC): Rushing efficiency
  → 4.5+ is good, 5.0+ is excellent
- True Catch Rate: Catchable targets caught
  → Better than raw catch % for WR evaluation
- Broken Tackles: Missed tackles forced
  → Indicates elusiveness and big play ability

SITUATION METRICS:
- Game Script: Expected point differential
  → Positive = more passing, Negative = more rushing
- Neutral Game Script %: Snaps in neutral situations
  → Better indicator of true role than blowouts
- Two-Minute Drill Usage: Involvement in hurry-up
  → Indicates trust and pass-catching ability
- Goal Line Carries: Touches inside 5-yard line
  → TD equity indicator for RBs

OPPORTUNITY METRICS:
- Expected Fantasy Points (xFP): Based on usage
  → Compare actual vs expected to find efficiency
- Opportunity Share: Team offense share
  → Volume is king in fantasy football
- Slot Rate: % of snaps in slot for WRs
  → Slot WRs see more targets in PPR
- Route Participation: % of pass plays running route
  → 90%+ indicates featured receiver

QUARTERBACK METRICS:
- Time to Throw: Average release time
  → Affects WR separation and completion %
- Play Action %: % of dropbacks using play action
  → Higher = more big plays downfield
- Pressure Rate: % of dropbacks under pressure
  → Affects turnovers and efficiency
- Deep Ball %: % of throws 20+ yards
  → Indicates downfield aggression

SKILL POSITION TRENDS:
- Trending Up: Increased snap %, target share, touches
- Trending Down: Decreased involvement or efficiency
- Consistent: Stable role week-to-week
- Volatile: Boom/bust performances

KEY TAKEAWAYS:
→ Volume > Talent in fantasy (especially early season)
→ Opportunity + Role > Efficiency alone
→ Target RBs with 15+ touches and WRs with 20%+ target share
→ Red zone usage is most predictive of TDs
→ Monitor snap counts for emerging players"""


@server.resource("guide://playoff-strategies")
def get_playoff_strategies() -> str:
    """Provide strategies for fantasy football playoffs."""
    return """Fantasy Football Playoff Strategies:

ROSTER CONSTRUCTION FOR PLAYOFFS:
✓ Handcuff elite RBs (injury insurance)
✓ Drop low-floor bench players
✓ Prioritize favorable playoff schedules (Weeks 15-17)
✓ Stream defense matchups
✓ Have backup plans for every position
✓ Consolidate depth via trades before deadline
✓ Target players returning from injury

PRE-PLAYOFF PREPARATION (Weeks 12-14):
1. Analyze Week 15-17 schedules for all players
2. Identify teams likely to rest starters (Week 17)
3. Target defenses playing poor offenses in playoffs
4. Trade away future value for immediate upgrades
5. Prioritize players on pass-heavy offenses
6. Stock handcuffs for your RB1/RB2
7. Drop players on bye in Week 14

CHAMPIONSHIP WEEK STRATEGY (Week 16-17):
- Weather is critical (snow/wind affects passing)
- Monitor news for resting starters
- Indoor games safer than outdoor in December
- Volume over talent for borderline decisions
- Trust proven performers over hot waiver adds
- Have Saturday replacements for Sunday players
- Check Vegas lines (blowouts = less volume for studs)

PLAYOFF SCHEDULE ANALYSIS:
GOOD PLAYOFF MATCHUPS (Target):
- Bad pass defenses (allows 250+ pass yards/game)
- Bad run defenses (allows 130+ rush yards/game)
- High-scoring offenses (creates game script)
- Dome games in late December (weather-proof)
- Teams eliminated from playoffs (less effort)

BAD PLAYOFF MATCHUPS (Avoid):
- Elite defenses (top 5 in points allowed)
- Divisional revenge games (extra motivation)
- Cold weather games for warm weather teams
- Week 17 locked playoff seeds (rest risk)
- Backup QBs or depleted offenses

POSITIONAL STRATEGY:

QUARTERBACK:
- Target high-volume passers (35+ attempts)
- Prefer indoor or warm-weather games
- Avoid QBs on run-heavy teams in playoffs
- Stream based on matchup if no elite option

RUNNING BACK:
- Handcuff all workhorse RBs
- Target RBs with bellcow usage (20+ touches)
- Avoid RBBC situations in playoffs
- Monitor for rest in Week 17 for playoff teams
- Prefer pass-catching backs in PPR

WIDE RECEIVER:
- Target high-volume WRs (8+ targets)
- Slot receivers safer in bad weather
- Deep threats risky in wind/snow
- WR1s on team safer than WR2/3
- Avoid rookie QBs throwing in bad weather

TIGHT END:
- Elite TEs (Kelce tier) are matchup-proof
- Stream TEs against bad defenses otherwise
- Red zone usage critical for TE scoring
- Volume matters more than talent

FLEX DECISIONS:
- Prefer RBs over WRs in bad weather
- WRs have higher ceiling in good matchups
- TEs are floor plays (safe but low ceiling)
- Trust your studs over waiver wire adds
- Volume > Matchup for borderline decisions

DEFENSE/KICKER STREAMING:
- Stream defense vs bad offenses
- Target defenses at home in bad weather
- Kickers in domes for consistency
- Avoid defenses vs elite QBs

WEEK 17 CONSIDERATIONS:
⚠️ Teams with locked playoff seeds may rest starters
⚠️ Monitor Saturday injury reports closely
⚠️ Have backup plans for every starter
⚠️ Avoid players on locked 1-seed teams
⚠️ Target teams fighting for playoff spots

CHAMPIONSHIP MENTALITY:
💪 Trust the players who got you here
💪 Don't overthink lineup decisions
💪 Weather and game script matter most
💪 Volume and opportunity = floor
💪 Have contingency plans ready
💪 Championship = bold moves + smart process"""


@server.resource("guide://dynasty-keeper")
def get_dynasty_keeper_guide() -> str:
    """Provide strategies for dynasty and keeper leagues."""
    return """Dynasty & Keeper League Strategy Guide:

DYNASTY LEAGUE FUNDAMENTALS:
- Player values span multiple years
- Youth and upside trump proven veterans
- Draft picks are valuable trade assets
- Rebuild vs compete decisions critical
- Contracts and cap space management (if applicable)
- Deeper benches (25-30+ roster spots typical)

KEEPER LEAGUE FUNDAMENTALS:
- Keep 1-5 players year-to-year (league dependent)
- Keeper cost tied to draft position or auction $
- Balance current year vs future value
- Late-round picks provide keeper value
- Drop players with bad keeper value late season

VALUATION DIFFERENCES (Dynasty vs Redraft):

POSITIONS TO PRIORITIZE:
1. Elite Young RBs (age 22-25)
   → Rare asset with multi-year value
2. Young WRs with target share (age 22-27)
   → Longer careers than RBs, safer dynasty assets
3. Young elite TEs (age 23-26)
   → Kelce/Andrews tier, decade-long value
4. Top 5 QBs in Superflex
   → Game-breaking advantage in Superflex formats

ROOKIE DRAFT STRATEGY:
- Early picks = high-capital NFL draft picks
- Target landing spot + draft capital combination
- RBs have shorter shelf life but immediate impact
- WRs take 2-3 years to develop typically
- QBs in Superflex leagues = premium value
- Avoid reaching for need (value > need in dynasty)

PLAYER LIFECYCLE MANAGEMENT:

CONTENDING TEAMS (Win Now):
→ Trade future picks for proven vets
→ Target players aged 26-29 (prime years)
→ Package young players for upgrades
→ Stream and optimize for current season
→ Don't hold onto taxi squad guys

REBUILDING TEAMS (2+ Years Out):
→ Trade aging vets for picks
→ Acquire young players with upside
→ Take on injured players for discount
→ Don't compete half-way (commit to rebuild)
→ Accumulate draft capital (1sts and 2nds)

AGING CURVE BY POSITION:
- RB: Peak age 24-27, cliff at 28-30
- WR: Peak age 25-29, productive to 32+
- TE: Peak age 25-30, productive to 33+
- QB: Peak age 27-35, can play to 40+

TRADE STRATEGY:

SELLING WINDOW (Trade Before Value Drops):
- RBs aged 28+ (especially with injuries)
- WRs aged 31+ (target win-now teams)
- Players on contract years (uncertainty)
- Boom/bust players after hot streak
- Backup RBs before starter returns

BUYING WINDOW (Acquire at Discount):
- Injured players from contenders
- Rookies after slow start (patience pays)
- Players in bad offenses (situation change)
- Young WRs breaking out (buy early)
- Players on new teams (positive change)

DRAFT PICK VALUES:
- 1st Round Picks: Premium assets (especially early)
- 2nd Round Picks: Solid value, trade fodder
- 3rd+ Round Picks: Dart throws, low hit rate

TYPICAL PICK VALUE (Dynasty):
- Early 1st (1.01-1.03): Established WR2/RB2
- Mid 1st (1.04-1.08): Young WR2 or aging RB1
- Late 1st (1.09-1.12): WR3 with upside or TE1
- Early 2nd: High-upside WR or backup RB
- Mid/Late 2nd: Bench depth or taxi squad stash

KEEPER LEAGUE SPECIFIC:

KEEPER VALUE CALCULATION:
- Keep cost vs Expected draft position
- Years of keeper eligibility remaining
- Contract escalation (if applicable)
- Opportunity cost of keeper slot

BEST KEEPER VALUES:
✓ Late round picks who broke out (round 10+ keepers)
✓ Rookies drafted late who hit (league-winning value)
✓ Injured players stashed (return to form)
✓ Young QBs in Superflex (early breakouts)

AVOID KEEPING:
✗ Early round picks (no value gain)
✗ Aging RBs (value cliff coming)
✗ Players with bad contracts (auction leagues)
✗ Injury-prone vets (risk > reward)

KEY DIFFERENCES VS REDRAFT:
📊 Think 2-3 years ahead, not just this season
📊 Age matters more than current production
📊 Target situation + talent over production only
📊 Rebuild fully or compete fully (no half-measures)
📊 Draft picks are tradeable assets with real value
📊 Patience is rewarded (develop young players)
📊 Deeper benches = more roster management"""


def run_http_server(
    host: Optional[str] = None, port: Optional[int] = None, *, show_banner: bool = True
) -> None:
    """Start the FastMCP server using the HTTP transport."""

    resolved_host = host or os.getenv("HOST", "0.0.0.0")
    resolved_port = port or int(os.getenv("PORT", "8000"))

    server.run(
        "http",
        host=resolved_host,
        port=resolved_port,
        show_banner=show_banner,
    )


def main() -> None:
    """Console script entry point for launching the HTTP server."""

    run_http_server()


__all__ = [
    "server",
    "run_http_server",
    "main",
    # Core Tools
    "ff_get_leagues",
    "ff_get_league_info",
    "ff_get_standings",
    "ff_get_roster",
    "ff_get_matchup",
    "ff_get_players",
    "ff_compare_teams",
    "ff_build_lineup",
    "ff_refresh_token",
    "ff_get_api_status",
    "ff_clear_cache",
    "ff_get_draft_results",
    "ff_get_waiver_wire",
    "ff_get_draft_rankings",
    "ff_get_draft_recommendation",
    "ff_analyze_draft_state",
    "ff_analyze_reddit_sentiment",
    # Prompts - Pre-built prompt templates for LLMs
    "analyze_roster_strengths",
    "draft_strategy_advice",
    "matchup_analysis",
    "waiver_wire_priority",
    "trade_evaluation",
    "start_sit_decision",
    "bye_week_planning",
    "playoff_preparation",
    "trade_proposal_generation",
    "injury_replacement_strategy",
    "streaming_dst_kicker",
    "season_long_strategy_check",
    "weekly_game_plan",
    # Resources - Reference data for LLM context
    "get_scoring_rules",
    "get_position_info",
    "get_draft_strategies",
    "get_injury_status_info",
    "get_weekly_strategy_guide",
    "get_common_mistakes_guide",
    "get_advanced_stats_glossary",
    "get_playoff_strategies",
    "get_dynasty_keeper_guide",
    "get_tool_selection_guide",
    "get_version",
]

# Optional resource: expose deployed commit SHA for diagnostics
try:
    with open(os.path.join(os.path.dirname(__file__), "COMMIT_SHA"), "r", encoding="utf-8") as _f:
        _COMMIT_SHA = _f.read().strip()
except Exception:  # pragma: no cover - best effort
    _COMMIT_SHA = "unknown"


@server.resource("guide://tool-selection")
def get_tool_selection_guide() -> str:
    """Comprehensive guide for LLMs on when and how to use fantasy football tools."""
    return json.dumps(
        {
            "title": "Fantasy Football Tool Selection Guide for LLMs",
            "description": "Strategic guidance for AI assistants on optimal tool usage patterns",
            "workflow_priority": [
                "1. START: ff_get_leagues - Always begin here if you don't have a league_key",
                "2. CONTEXT: ff_get_league_info - Understand league settings and scoring",
                "3. BASELINE: ff_get_roster - Know current lineup before making recommendations",
                "4. COMPETITION: ff_get_matchup - Analyze weekly opponent for strategic adjustments",
                "5. OPPORTUNITIES: ff_get_waiver_wire - Identify available upgrades",
                "6. OPTIMIZATION: ff_build_lineup - AI-powered lineup construction",
            ],
            "tool_categories": {
                "CORE_LEAGUE_DATA": {
                    "description": "Essential league information and setup",
                    "tools": {
                        "ff_get_leagues": "Discovery: Find available leagues and extract league_key identifiers",
                        "ff_get_league_info": "Configuration: League settings, scoring rules, roster requirements",
                        "ff_get_standings": "Rankings: Current standings, records, points for strategy context",
                    },
                },
                "PLAYER_ROSTER_ANALYSIS": {
                    "description": "Player and roster management tools",
                    "tools": {
                        "ff_get_roster": "Current Lineup: Configurable roster data (basic/standard/full detail levels) for lineup decisions",
                        "ff_get_players": "Player Search: Find specific players by name or position",
                        "ff_get_waiver_wire": "Free Agents: Available players with advanced metrics",
                    },
                },
                "MATCHUP_COMPETITION": {
                    "description": "Head-to-head analysis and competitive intelligence",
                    "tools": {
                        "ff_get_matchup": "Opponent Analysis: Weekly head-to-head strategic insights",
                        "ff_compare_teams": "Team Comparison: Direct roster and performance comparisons",
                    },
                },
                "OPTIMIZATION_STRATEGY": {
                    "description": "AI-powered decision making and strategy tools",
                    "tools": {
                        "ff_build_lineup": "AI Optimization: Championship-level lineup recommendations with positional constraints",
                        "ff_get_draft_rankings": "Player Tiers: Value assessment and tier-based rankings",
                        "ff_analyze_reddit_sentiment": "Market Intelligence: Public opinion and trending players",
                    },
                },
                "ADVANCED_ANALYSIS": {
                    "description": "Deep analytics and historical insights",
                    "tools": {
                        "ff_get_draft_results": "Draft History: Historical patterns and team building analysis",
                        "ff_analyze_draft_state": "Live Draft: Real-time draft strategy and recommendations",
                    },
                },
                "UTILITY_MAINTENANCE": {
                    "description": "System maintenance and troubleshooting",
                    "tools": {
                        "ff_refresh_token": "Authentication: Fix Yahoo API authentication issues",
                        "ff_get_api_status": "Health Check: Verify system status and connectivity",
                        "ff_clear_cache": "Reset: Clear cached data for fresh analysis",
                    },
                },
            },
            "strategic_usage_patterns": {
                "weekly_lineup_optimization": [
                    "ff_get_leagues -> ff_get_roster -> ff_get_matchup -> ff_get_waiver_wire -> ff_build_lineup"
                ],
                "draft_preparation": [
                    "ff_get_leagues -> ff_get_league_info -> ff_get_draft_rankings -> ff_analyze_draft_state"
                ],
                "competitive_analysis": [
                    "ff_get_league_info -> ff_get_standings -> ff_compare_teams -> ff_get_matchup"
                ],
                "market_research": [
                    "ff_get_waiver_wire -> ff_analyze_reddit_sentiment -> ff_get_players"
                ],
            },
            "decision_framework": {
                "data_gathering": "Always start with league discovery and current roster state",
                "context_building": "Understand league settings, scoring, and competitive landscape",
                "opportunity_identification": "Use waiver wire and sentiment analysis for edge cases",
                "optimization": "Apply AI-powered tools for championship-level recommendations",
                "validation": "Cross-reference multiple data sources for confident decisions",
            },
            "best_practices": [
                "NEVER guess league_key - always use ff_get_leagues first",
                "ALWAYS check current roster before making lineup recommendations",
                "USE ff_get_matchup for opponent-specific weekly strategy",
                "LEVERAGE ff_analyze_reddit_sentiment for contrarian plays",
                "APPLY strategy parameters in ff_build_lineup for optimized construction",
                "COMBINE multiple tools for comprehensive decision making",
            ],
        }
    )


@server.resource("meta://version")
def get_version() -> str:  # pragma: no cover - simple accessor
    return json.dumps({"commit": _COMMIT_SHA})


if __name__ == "__main__":
    main()
