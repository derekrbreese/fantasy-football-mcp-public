"""
Utility module for loading and managing NFL bye week data.

Provides static bye week data as a fallback when API data is missing or invalid.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache for loaded bye week data to avoid repeated file reads
_BYE_WEEK_CACHE: Optional[Dict[str, int]] = None


def load_static_bye_weeks() -> Dict[str, int]:
    """
    Load static bye week data from JSON file.
    
    Returns:
        Dictionary mapping team abbreviations to bye week numbers.
        Returns empty dict if file cannot be loaded.
    """
    global _BYE_WEEK_CACHE
    
    # Return cached data if available
    if _BYE_WEEK_CACHE is not None:
        return _BYE_WEEK_CACHE
    
    try:
        # Get the path to the static data file
        data_file = Path(__file__).parent.parent / "data" / "bye_weeks_2025.json"
        
        with open(data_file, 'r') as f:
            bye_weeks = json.load(f)
        
        # Validate the data structure
        if not isinstance(bye_weeks, dict):
            logger.error("Static bye week data is not a dictionary")
            return {}
        
        # Validate all values are integers between 1 and 18
        for team, week in bye_weeks.items():
            if not isinstance(week, int) or not (1 <= week <= 18):
                logger.warning(f"Invalid bye week {week} for team {team} in static data")
        
        # Cache the loaded data
        _BYE_WEEK_CACHE = bye_weeks
        logger.info(f"Loaded static bye week data for {len(bye_weeks)} teams")
        
        return bye_weeks
        
    except FileNotFoundError:
        logger.error("Static bye week data file not found")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing static bye week data: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error loading static bye week data: {e}")
        return {}


def get_bye_week_with_fallback(
    team_abbr: str,
    api_bye_week: Optional[int] = None
) -> Optional[int]:
    """
    Get bye week for a team, preferring the value Yahoo returned.

    Yahoo's value is for the season being queried, so it wins whenever it is a
    valid week. The static file is a single season's schedule (2025) and is only
    consulted when Yahoo gives nothing usable.

    Args:
        team_abbr: Team abbreviation (e.g., "KC", "SF", "BUF")
        api_bye_week: Bye week from the API, if available

    Returns:
        Bye week number (1-18) or None if not found.
    """
    if isinstance(api_bye_week, int) and not isinstance(api_bye_week, bool) and 1 <= api_bye_week <= 18:
        return api_bye_week

    static_data = load_static_bye_weeks()
    if team_abbr in static_data:
        logger.debug(f"No usable API bye week for {team_abbr}; using static value")
        return static_data[team_abbr]

    logger.warning(f"No bye week data found for team {team_abbr} (static or API)")
    return None


def build_team_bye_week_map(
    api_team_data: Optional[Dict[str, int]] = None
) -> Dict[str, int]:
    """
    Build a complete team-to-bye-week mapping with fallback support.
    
    Combines API data (if available) with static data to ensure all teams
    have bye week information.
    
    Args:
        api_team_data: Optional dictionary of team abbreviations to bye weeks from API
    
    Returns:
        Dictionary mapping team abbreviations to bye week numbers.
    """
    # Start with static data as baseline
    bye_week_map = load_static_bye_weeks().copy()
    
    # Override with API data where available and valid
    if api_team_data:
        valid_count = 0
        invalid_count = 0
        
        for team, week in api_team_data.items():
            if isinstance(week, int) and 1 <= week <= 18:
                bye_week_map[team] = week
                valid_count += 1
            else:
                invalid_count += 1
                logger.warning(f"Ignoring invalid API bye week {week} for {team}")
        
        if valid_count > 0:
            logger.info(f"Updated {valid_count} teams with API bye week data")
        if invalid_count > 0:
            logger.info(f"Kept static data for {invalid_count} teams due to invalid API data")
    else:
        logger.info("No API bye week data provided, using all static data")
    
    return bye_week_map


def clear_cache():
    """Clear the cached bye week data. Useful for testing or forcing a reload."""
    global _BYE_WEEK_CACHE
    _BYE_WEEK_CACHE = None
    logger.debug("Bye week cache cleared")