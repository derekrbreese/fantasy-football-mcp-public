"""MCP tool handlers - orchestrates handler modules with dependency injection."""

# Import simple handlers from dedicated modules (no dependencies)
from .admin_handlers import (
    handle_ff_clear_cache,
    handle_ff_get_api_status,
    handle_ff_refresh_token,
)

# League handlers (need helper function injection)
from .league_handlers import (
    handle_ff_get_league_info,
    handle_ff_get_leagues,
    handle_ff_get_scoreboard,
    handle_ff_get_standings,
    handle_ff_get_teams,
    handle_ff_get_transactions,
)

# Roster handlers (need dependency injection)
from .roster_handlers import handle_ff_get_roster

# Matchup handlers (need dependency injection)
from .matchup_handlers import (
    handle_ff_build_lineup,
    handle_ff_compare_teams,
    handle_ff_get_matchup,
)

# Player handlers (need dependency injection)
from .player_handlers import (
    handle_ff_get_players,
    handle_ff_get_waiver_wire,
)

# Draft handlers (need dependency injection)
from .draft_handlers import (
    handle_ff_analyze_draft_state,
    handle_ff_get_draft_rankings,
    handle_ff_get_draft_recommendation,
    handle_ff_get_draft_results,
)

# Analytics handlers (minimal dependencies)
from .analytics_handlers import handle_ff_analyze_reddit_sentiment


def inject_roster_dependencies(**deps):
    """Inject dependencies needed by roster handlers.

    Required dependencies:
    - get_user_team_info: Get user's team info in a league
    - yahoo_api_call: Make Yahoo API calls
    - parse_team_roster: Parse roster from Yahoo API response
    """
    import src.handlers.roster_handlers as roster_mod

    for name, func in deps.items():
        setattr(roster_mod, name, func)


def inject_matchup_dependencies(**deps):
    """Inject dependencies needed by matchup handlers.

    Required dependencies:
    - get_user_team_key: Get user's team key in a league
    - get_user_team_info: Get user's team info in a league
    - yahoo_api_call: Make Yahoo API calls
    - parse_team_roster: Parse roster from Yahoo API response
    """
    import src.handlers.matchup_handlers as matchup_mod

    for name, func in deps.items():
        setattr(matchup_mod, name, func)


def inject_player_dependencies(**deps):
    """Inject dependencies needed by player handlers.

    Required dependencies:
    - yahoo_api_call: Make Yahoo API calls
    - get_waiver_wire_players: Get waiver wire players
    """
    import src.handlers.player_handlers as player_mod

    for name, func in deps.items():
        setattr(player_mod, name, func)


def inject_draft_dependencies(**deps):
    """Inject dependencies needed by draft handlers.

    Required dependencies:
    - get_all_teams_info: Get all teams info
    - get_draft_rankings: Get draft rankings
    - get_draft_recommendation_simple: Get draft recommendations
    - analyze_draft_state_simple: Analyze draft state
    - DRAFT_AVAILABLE: Draft availability flag
    """
    import src.handlers.draft_handlers as draft_mod

    for name, value in deps.items():
        setattr(draft_mod, name, value)


def inject_league_helpers(**helpers):
    """Inject helper functions needed by league handlers.

    League handlers need access to discover_leagues, get_user_team_info,
    and get_all_teams_info which use global state.
    """
    import src.handlers.league_handlers as league_mod

    for name, func in helpers.items():
        setattr(league_mod, name, func)


__all__ = [
    # Admin handlers (fully extracted)
    "handle_ff_refresh_token",
    "handle_ff_get_api_status",
    "handle_ff_clear_cache",
    # League handlers (extracted, need helper injection)
    "handle_ff_get_leagues",
    "handle_ff_get_league_info",
    "handle_ff_get_standings",
    "handle_ff_get_teams",
    "handle_ff_get_scoreboard",
    "handle_ff_get_transactions",
    # Roster handlers (extracted, need dependency injection)
    "handle_ff_get_roster",
    # Matchup handlers (extracted, need dependency injection)
    "handle_ff_get_matchup",
    "handle_ff_build_lineup",
    "handle_ff_compare_teams",
    # Player handlers (extracted, need dependency injection)
    "handle_ff_get_players",
    "handle_ff_get_waiver_wire",
    # Draft handlers (extracted, need dependency injection)
    "handle_ff_get_draft_results",
    "handle_ff_get_draft_rankings",
    "handle_ff_get_draft_recommendation",
    "handle_ff_analyze_draft_state",
    # Analytics handlers (extracted, minimal dependencies)
    "handle_ff_analyze_reddit_sentiment",
    # Injection functions
    "inject_roster_dependencies",
    "inject_matchup_dependencies",
    "inject_player_dependencies",
    "inject_draft_dependencies",
    "inject_league_helpers",
]
