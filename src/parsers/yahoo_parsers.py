"""Parsers for Yahoo Fantasy Sports API responses."""

from typing import Any, Dict, List, Optional

from src.utils.bye_weeks import get_bye_week_with_fallback


def _extract_positions(obj: Any) -> List[str]:
    """Flatten Yahoo's eligible_positions into a list of position strings.

    Yahoo's JSON uses ``[{"position": "WR"}, {"position": "W/R/T"}]``; XML-derived
    payloads use ``{"position": ["WR", "W/R/T"]}``. Both, plus plain strings, work.
    """
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        return [p for item in obj for p in _extract_positions(item)]
    if isinstance(obj, dict):
        if "position" in obj:
            return _extract_positions(obj["position"])
        return [p for key, value in obj.items() if key != "count" for p in _extract_positions(value)]
    return []


def parse_team_roster(data: Dict) -> List[Dict]:
    """Extract a simple roster list from Yahoo team data.

    Args:
        data: Raw Yahoo API response from team/{team_key}/roster endpoint

    Returns:
        List of player dictionaries with name, position, team, status
    """
    roster: List[Dict] = []
    team = data.get("fantasy_content", {}).get("team", [])

    for item in team:
        if isinstance(item, dict) and "roster" in item:
            roster_data = item["roster"]
            players = None
            if isinstance(roster_data, dict):
                players = roster_data.get("0", {}).get("players")
                if not players:
                    players = roster_data.get("players")  # Direct players key
                if not players and "roster" in roster_data:
                    players = roster_data["roster"].get("players")
            if not players:
                print(
                    f"DEBUG: No players in roster_data for item {item.get('roster', {}).keys() if isinstance(item.get('roster'), dict) else type(item.get('roster'))}"
                )
                continue

            for key, pdata in players.items():
                if key == "count" or not isinstance(pdata, dict) or "player" not in pdata:
                    continue

                player_array = pdata["player"]
                if not isinstance(player_array, list):
                    continue

                info: Dict[str, Any] = {}

                # Helper to robustly extract selected position regardless of structure
                def _extract_position(selected_position_obj: Any) -> Optional[str]:
                    if not selected_position_obj:
                        return None
                    if isinstance(selected_position_obj, dict):
                        # Direct form: {"position": "WR"}
                        if "position" in selected_position_obj:
                            return selected_position_obj.get("position")
                        # Keyed form: {"0": {"position": "WR"}, "count": 1}
                        for k, v in selected_position_obj.items():
                            if k == "count":
                                continue
                            if isinstance(v, dict) and "position" in v:
                                return v["position"]
                    elif isinstance(selected_position_obj, list):
                        for entry in selected_position_obj:
                            if isinstance(entry, dict) and "position" in entry:
                                return entry["position"]
                    return None

                def _scan_container(container: Any) -> None:
                    if not isinstance(container, dict):
                        return
                    name_dict = container.get("name")
                    if isinstance(name_dict, dict) and "full" in name_dict:
                        info["name"] = name_dict.get("full")
                    # Status, default will be set later if absent
                    if "status" in container:
                        info["status"] = container.get("status", "OK")
                    # Prefer selected_position when available
                    if "selected_position" in container:
                        pos = _extract_position(container.get("selected_position"))
                        if pos:
                            info["position"] = pos
                    # Fallback to display position if selected_position not found
                    if "position" not in info and "display_position" in container:
                        info["position"] = container.get("display_position")
                    # The player's real positions, kept apart from the slot he occupies,
                    # so the optimizer can move bench players into any slot they fit.
                    if "display_position" in container:
                        info["display_position"] = container.get("display_position")
                    if "eligible_positions" in container:
                        eligible = _extract_positions(container.get("eligible_positions"))
                        if eligible:
                            info["eligible_positions"] = eligible

                    if "team" not in info:
                        team_value: Optional[str] = None

                        # Direct keys commonly present in Yahoo responses
                        for key in (
                            "editorial_team_abbr",
                            "team_abbr",
                            "team_abbreviation",
                            "editorial_team_full_name",
                            "editorial_team_name",
                        ):
                            value = container.get(key)
                            if isinstance(value, str) and value.strip():
                                team_value = value
                                break

                        # Nested team objects occasionally hold the abbreviation/name
                        if not team_value and isinstance(container.get("team"), dict):
                            team_container = container["team"]
                            for nested_key in ("abbr", "abbreviation", "name", "nickname"):
                                nested_value = team_container.get(nested_key)
                                if isinstance(nested_value, str) and nested_value.strip():
                                    team_value = nested_value
                                    break

                        if team_value:
                            info["team"] = team_value

                    if "bye_weeks" in container:
                        bye_weeks_data = container["bye_weeks"]
                        if isinstance(bye_weeks_data, dict) and "week" in bye_weeks_data:
                            bye_week = bye_weeks_data.get("week")
                            if bye_week and str(bye_week).isdigit():
                                bye_num = int(bye_week)
                                if 1 <= bye_num <= 18:
                                    info["bye"] = bye_num
                                else:
                                    info["bye"] = None
                            else:
                                info["bye"] = None
                        else:
                            info["bye"] = None

                for element in player_array:
                    if isinstance(element, dict):
                        _scan_container(element)
                    elif isinstance(element, list):
                        for sub in element:
                            _scan_container(sub)

                if info:
                    if "status" not in info:
                        info["status"] = "OK"

                    team_abbr = info.get("team")
                    api_bye_week = info.get("bye") if isinstance(info.get("bye"), int) else None
                    if isinstance(team_abbr, str) and team_abbr:
                        resolved_bye = get_bye_week_with_fallback(team_abbr, api_bye_week)
                        info["bye"] = resolved_bye
                    else:
                        info["bye"] = None

                    roster.append(info)

    return roster


def parse_yahoo_free_agent_players(data: Dict) -> List[Dict]:
    """Extract free agent/waiver players from Yahoo data, similar to team roster.

    Args:
        data: Raw Yahoo API response from league/{league_key}/players endpoint

    Returns:
        List of player dictionaries with name, position, team, ownership stats
    """
    players: List[Dict] = []
    league = data.get("fantasy_content", {}).get("league", [])

    # Find players section (typically league[1]["players"])
    if len(league) > 1 and isinstance(league[1], dict) and "players" in league[1]:
        players_data = league[1]["players"]

        for key, pdata in players_data.items():
            if key == "count" or not isinstance(pdata, dict) or "player" not in pdata:
                continue

            player_array = pdata["player"]
            if not isinstance(player_array, list):
                continue

            info: Dict[str, Any] = {}

            def _scan_free_agent(container: Any) -> None:
                if not isinstance(container, dict):
                    return
                # Name
                name_dict = container.get("name")
                if isinstance(name_dict, dict) and "full" in name_dict:
                    info["name"] = name_dict.get("full")
                # Position
                if "display_position" in container:
                    info["position"] = container.get("display_position")
                # Team
                if "editorial_team_abbr" in container:
                    info["team"] = container["editorial_team_abbr"]
                elif "team" in container and isinstance(container["team"], dict):
                    info["team"] = container["team"].get("abbr") or container["team"].get(
                        "abbreviation"
                    )
                # Ownership
                if "ownership" in container and isinstance(container["ownership"], dict):
                    info["owned_pct"] = container["ownership"].get("ownership_percentage", 0)
                    info["weekly_change"] = container["ownership"].get("weekly_change", 0)
                if "percent_owned" in container:
                    info["owned_pct"] = container["percent_owned"]
                # Injury
                if "status" in container:
                    info["injury_status"] = container["status"]
                if "status_full" in container:
                    info["injury_detail"] = container["status_full"]
                # Bye week extraction with validation
                if "bye_weeks" in container:
                    bye_weeks_data = container["bye_weeks"]
                    if isinstance(bye_weeks_data, dict) and "week" in bye_weeks_data:
                        bye_week = bye_weeks_data.get("week")
                        # Validate bye week is a valid week number (1-18)
                        if bye_week and str(bye_week).isdigit():
                            bye_num = int(bye_week)
                            if 1 <= bye_num <= 18:
                                info["bye"] = bye_num
                            else:
                                info["bye"] = None
                        else:
                            info["bye"] = None
                    else:
                        info["bye"] = None
                else:
                    # No bye_weeks field present
                    info["bye"] = None

            for element in player_array:
                if isinstance(element, dict):
                    _scan_free_agent(element)
                elif isinstance(element, list):
                    for sub in element:
                        _scan_free_agent(sub)

            if info and info.get("name"):
                players.append(info)

    return players
