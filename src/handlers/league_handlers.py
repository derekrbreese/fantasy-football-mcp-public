"""League-level MCP tool handlers (leagues, standings, teams, scoreboard, transactions)."""

from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.api import yahoo_api_call


# These functions need to be imported from main file since they use global cache
# We'll import them when updating fantasy_football_multi_league.py
async def discover_leagues():
    """Placeholder - will be imported from main module."""
    raise NotImplementedError("Must import from fantasy_football_multi_league")


async def get_user_team_info(league_key):
    """Placeholder - will be imported from main module."""
    raise NotImplementedError("Must import from fantasy_football_multi_league")


async def get_all_teams_info(league_key):
    """Placeholder - will be imported from main module."""
    raise NotImplementedError("Must import from fantasy_football_multi_league")


async def handle_ff_get_leagues(arguments: Dict) -> Dict:
    """Get all fantasy football leagues for the authenticated user.

    Args:
        arguments: Empty dict (no arguments required)

    Returns:
        Dict with total_leagues and list of league summaries
    """
    leagues = await discover_leagues()

    if not leagues:
        return {
            "error": "No active NFL leagues found",
            "suggestion": "Make sure your Yahoo token is valid and you have active leagues",
        }

    return {
        "total_leagues": len(leagues),
        "leagues": [
            {
                "key": league["key"],
                "name": league["name"],
                "teams": league["num_teams"],
                "current_week": league["current_week"],
                "scoring": league["scoring_type"],
            }
            for league in leagues.values()
        ],
    }


async def handle_ff_get_league_info(arguments: Dict) -> Dict:
    """Get detailed information about a specific league.

    Args:
        arguments: Dict with 'league_key'

    Returns:
        Dict with league details and your team summary
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key = arguments.get("league_key")

    leagues = await discover_leagues()
    if league_key not in leagues:
        return {
            "error": f"League {league_key} not found",
            "available_leagues": list(leagues.keys()),
        }

    league = leagues[league_key]
    team_info = await get_user_team_info(league_key)
    _ = await yahoo_api_call(f"league/{league_key}")

    return {
        "league": league["name"],
        "key": league_key,
        "season": league["season"],
        "teams": league["num_teams"],
        "current_week": league["current_week"],
        "scoring_type": league["scoring_type"],
        "status": "active" if not league["is_finished"] else "finished",
        "your_team": {
            "name": team_info.get("team_name", "Unknown") if team_info else "Not found",
            "key": team_info.get("team_key") if team_info else None,
            "draft_position": team_info.get("draft_position") if team_info else None,
            "draft_grade": team_info.get("draft_grade") if team_info else None,
        },
    }


async def handle_ff_get_standings(arguments: Dict) -> Dict:
    """Get current standings for a league.

    Args:
        arguments: Dict with 'league_key'

    Returns:
        Dict with league_key and sorted standings list
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key = arguments.get("league_key")
    data = await yahoo_api_call(f"league/{league_key}/standings")

    standings = []
    league = data.get("fantasy_content", {}).get("league", [])

    for item in league:
        if isinstance(item, dict) and "standings" in item:
            standings_list = item["standings"]
            teams = {}
            if isinstance(standings_list, list) and standings_list:
                teams = standings_list[0].get("teams", {})
            elif isinstance(standings_list, dict):
                teams = standings_list.get("teams", {})

            for key, team_entry in teams.items():
                if key == "count" or not isinstance(team_entry, dict):
                    continue
                if "team" in team_entry:
                    team_array = team_entry["team"]
                    team_info = {}
                    team_standings = {}
                    if isinstance(team_array, list) and team_array:
                        core = team_array[0]
                        if isinstance(core, list):
                            for elem in core:
                                if isinstance(elem, dict) and "name" in elem:
                                    team_info["name"] = elem["name"]
                        for part in team_array[1:]:
                            if isinstance(part, dict) and "team_standings" in part:
                                team_standings = part["team_standings"]

                    if team_info and team_standings:
                        # Yahoo mixes strings and numbers here ("rank": "10", "ties": 0),
                        # so coerce everything; string ranks would sort 1, 10, 11, 2.
                        totals = team_standings.get("outcome_totals") or {}
                        standings.append(
                            {
                                "rank": _to_int(team_standings.get("rank")) or 0,
                                "team": team_info.get("name", "Unknown"),
                                "wins": _to_int(totals.get("wins")) or 0,
                                "losses": _to_int(totals.get("losses")) or 0,
                                "ties": _to_int(totals.get("ties")) or 0,
                                "points_for": round(
                                    _to_float(team_standings.get("points_for")) or 0.0, 2
                                ),
                                "points_against": round(
                                    _to_float(team_standings.get("points_against")) or 0.0, 2
                                ),
                            }
                        )

    # Unranked teams (rank 0, e.g. before week 1) go last.
    standings.sort(key=lambda row: row["rank"] or 999)
    return {"league_key": league_key, "standings": standings}


async def handle_ff_get_teams(arguments: Dict) -> Dict:
    """Get all teams in a league.

    Args:
        arguments: Dict with 'league_key'

    Returns:
        Dict with league_key, teams list, and total_teams count
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key: Optional[str] = arguments.get("league_key")
    if league_key is None:
        return {"error": "league_key cannot be None"}

    teams_info = await get_all_teams_info(league_key)
    return {
        "league_key": league_key,
        "teams": teams_info,
        "total_teams": len(teams_info),
    }


# ---------------------------------------------------------------------------
# Shared helpers for Yahoo's JSON layout
#
# Yahoo returns collections as dicts keyed "0", "1", ... plus a "count" entry,
# and returns entity metadata as a list of single-key dicts (with stray empty
# lists mixed in). These helpers hide both quirks.
# ---------------------------------------------------------------------------


def _indexed_values(node: Any) -> Iterator[Any]:
    """Yield the items of a Yahoo collection, whether it is a list or a numbered dict."""
    if isinstance(node, list):
        yield from node
    elif isinstance(node, dict):
        numbered = [key for key in node if isinstance(key, str) and key.isdigit()]
        for key in sorted(numbered, key=int):
            yield node[key]


def _merge_parts(node: Any) -> Dict[str, Any]:
    """Flatten Yahoo's list-of-single-key-dicts metadata into one dict."""
    merged: Dict[str, Any] = {}
    if isinstance(node, dict):
        merged.update(node)
    elif isinstance(node, list):
        for part in node:
            for key, value in _merge_parts(part).items():
                merged.setdefault(key, value)
    return merged


def _find_section(data: Any, name: str) -> Any:
    """Return the named section from fantasy_content.league, or None."""
    league = (data or {}).get("fantasy_content", {}).get("league", [])
    if isinstance(league, dict):
        return league.get(name)
    for item in league if isinstance(league, list) else []:
        if isinstance(item, dict) and name in item:
            return item[name]
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round2(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _flag(value: Any) -> bool:
    return str(value) == "1"


def _points_total(node: Any) -> Optional[float]:
    return _to_float(node.get("total")) if isinstance(node, dict) else _to_float(node)


# ---------------------------------------------------------------------------
# Scoreboard
# ---------------------------------------------------------------------------


def _parse_scoreboard_team(entry: Any) -> Optional[Dict[str, Any]]:
    team = entry.get("team") if isinstance(entry, dict) else None
    if not isinstance(team, list) or not team:
        return None
    info = _merge_parts(team)
    if not info.get("team_key"):
        return None
    return {
        "team_key": info.get("team_key"),
        "name": info.get("name", "Unknown"),
        "points": _points_total(info.get("team_points")),
        "projected_points": _points_total(info.get("team_projected_points")),
        "win_probability": _round2(_to_float(info.get("win_probability"))),
    }


def _parse_scoreboard_matchup(entry: Any) -> Optional[Dict[str, Any]]:
    matchup = entry.get("matchup") if isinstance(entry, dict) else None
    if not isinstance(matchup, dict):
        return None

    teams: List[Dict[str, Any]] = []
    # Teams sit under matchup["0"]["teams"] or directly under matchup["teams"].
    for container in [matchup, *_indexed_values(matchup)]:
        if isinstance(container, dict) and "teams" in container:
            for team_entry in _indexed_values(container["teams"]):
                parsed = _parse_scoreboard_team(team_entry)
                if parsed:
                    teams.append(parsed)
    if not teams:
        return None

    return {
        "week": _to_int(matchup.get("week")),
        "status": matchup.get("status"),
        "is_playoffs": _flag(matchup.get("is_playoffs")),
        "is_consolation": _flag(matchup.get("is_consolation")),
        "is_tied": _flag(matchup.get("is_tied")),
        "winner_team_key": matchup.get("winner_team_key"),
        "teams": teams,
    }


async def handle_ff_get_scoreboard(arguments: Dict) -> Dict:
    """Get every matchup in a league for one week.

    Args:
        arguments: Dict with 'league_key' and optional 'week'

    Returns:
        Dict with league_key, week, and a matchups list (both teams per matchup)
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key = arguments["league_key"]
    week = _to_int(arguments.get("week"))
    week_param = f";week={week}" if week else ""
    data = await yahoo_api_call(f"league/{league_key}/scoreboard{week_param}")

    scoreboard = _find_section(data, "scoreboard")
    matchups: List[Dict[str, Any]] = []
    resolved_week = week

    if isinstance(scoreboard, dict):
        resolved_week = _to_int(scoreboard.get("week")) or week
        # Matchups normally sit under scoreboard["0"]["matchups"]; tolerate them one level up.
        containers = [scoreboard, *_indexed_values(scoreboard)]
        for container in containers:
            if isinstance(container, dict) and "matchups" in container:
                for entry in _indexed_values(container["matchups"]):
                    parsed = _parse_scoreboard_matchup(entry)
                    if parsed:
                        matchups.append(parsed)
                break

    if resolved_week is None and matchups:
        resolved_week = matchups[0]["week"]

    return {
        "league_key": league_key,
        "week": resolved_week,
        "matchups": matchups,
        "total_matchups": len(matchups),
    }


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

TRANSACTION_TYPES = ("add", "drop", "trade", "commish")
DEFAULT_TRANSACTION_COUNT = 25
MAX_TRANSACTION_COUNT = 100


def _normalize_transaction_types(value: Any) -> Optional[List[str]]:
    """Return a de-duplicated list of valid types, [] for none requested, None if invalid."""
    if value is None or value == "" or value == []:
        return []
    raw = value.split(",") if isinstance(value, str) else value
    if not isinstance(raw, (list, tuple)):
        return None
    cleaned: List[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if name not in TRANSACTION_TYPES:
            return None
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def _iso_timestamp(value: Any) -> Optional[str]:
    epoch = _to_int(value)
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _parse_transaction_player(entry: Any) -> Optional[Dict[str, Any]]:
    player = entry.get("player") if isinstance(entry, dict) else None
    if not isinstance(player, list) or not player:
        return None
    info = _merge_parts(player)
    # transaction_data is a one-item list for adds and a bare dict for drops.
    move = _merge_parts(info.get("transaction_data"))
    name = info.get("name")
    return {
        "player_key": info.get("player_key"),
        "name": name.get("full") if isinstance(name, dict) else name,
        "position": info.get("display_position"),
        "nfl_team": info.get("editorial_team_abbr"),
        "action": move.get("type"),
        "source_type": move.get("source_type"),
        "source_team": move.get("source_team_name"),
        "source_team_key": move.get("source_team_key"),
        "destination_type": move.get("destination_type"),
        "destination_team": move.get("destination_team_name"),
        "destination_team_key": move.get("destination_team_key"),
    }


def _parse_transaction(entry: Any) -> Optional[Dict[str, Any]]:
    transaction = entry.get("transaction") if isinstance(entry, dict) else None
    if not transaction:
        return None
    info = _merge_parts(transaction)
    if not info.get("transaction_key") and not info.get("type"):
        return None

    players = [
        parsed
        for parsed in map(_parse_transaction_player, _indexed_values(info.get("players")))
        if parsed
    ]

    result: Dict[str, Any] = {
        "transaction_key": info.get("transaction_key"),
        "type": info.get("type"),
        "status": info.get("status"),
        "timestamp": _iso_timestamp(info.get("timestamp")),
        "faab_bid": _to_int(info.get("faab_bid")),
        "players": players,
    }
    if info.get("type") == "trade":
        result["trader_team"] = info.get("trader_team_name")
        result["trader_team_key"] = info.get("trader_team_key")
        result["tradee_team"] = info.get("tradee_team_name")
        result["tradee_team_key"] = info.get("tradee_team_key")
    return result


# Which Yahoo filter returns each type, and which reported types satisfy it.
# A swap is reported as "add/drop", so it counts as both an add and a drop.
_YAHOO_TYPE_FAMILY = {"add": "add", "drop": "add", "trade": "trade", "commish": "commish"}
_TYPE_MATCHES = {
    "add": ("add", "add/drop"),
    "drop": ("drop", "add/drop"),
    "trade": ("trade",),
    "commish": ("commish",),
}


def _parse_transactions(data: Any) -> List[Dict[str, Any]]:
    return [
        parsed
        for parsed in map(
            _parse_transaction, _indexed_values(_find_section(data, "transactions"))
        )
        if parsed
    ]


async def handle_ff_get_transactions(arguments: Dict) -> Dict:
    """Get recent league transactions (adds, drops, trades, commissioner moves).

    Args:
        arguments: Dict with 'league_key', optional 'types' (list or comma string
            drawn from add, drop, trade, commish) and optional 'count' (1-100)

    Returns:
        Dict with league_key and a newest-first transactions list
    """
    if not arguments.get("league_key"):
        return {"error": "league_key is required"}

    league_key = arguments["league_key"]

    types = _normalize_transaction_types(arguments.get("types"))
    if types is None:
        return {"error": f"types must be drawn from: {', '.join(TRANSACTION_TYPES)}"}

    count = _to_int(arguments.get("count"))
    if count is None:
        count = DEFAULT_TRANSACTION_COUNT
    count = max(1, min(count, MAX_TRANSACTION_COUNT))

    if not types:
        data = await yahoo_api_call(f"league/{league_key}/transactions;count={count}")
        transactions = _parse_transactions(data)
    else:
        # Yahoo's filter is coarser than the types it reports: "add" and "drop" both
        # return every roster move, and a comma-separated list keeps only the first
        # family (asking for "add,trade" returns no trades). So ask once per family
        # and do the exact filtering here.
        wanted = {match for name in types for match in _TYPE_MATCHES[name]}
        families = list(dict.fromkeys(_YAHOO_TYPE_FAMILY[name] for name in types))
        found: Dict[str, Dict[str, Any]] = {}
        for family in families:
            data = await yahoo_api_call(
                f"league/{league_key}/transactions;types={family};count={MAX_TRANSACTION_COUNT}"
            )
            for parsed in _parse_transactions(data):
                if parsed.get("type") in wanted:
                    found.setdefault(parsed.get("transaction_key") or str(len(found)), parsed)
        transactions = sorted(
            found.values(), key=lambda row: row.get("timestamp") or "", reverse=True
        )[:count]

    return {
        "league_key": league_key,
        "types": types or "all",
        "transactions": transactions,
        "total_transactions": len(transactions),
    }
