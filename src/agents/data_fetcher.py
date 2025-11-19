"""
Yahoo Fantasy Sports API Data Fetcher Agent.

This module provides the DataFetcherAgent class that handles all Yahoo Fantasy Sports
API interactions including OAuth2 authentication, data fetching with rate limiting,
and intelligent caching through the cache manager.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import time

import aiohttp
from loguru import logger
from yfpy import YahooFantasySportsQuery
from yfpy.models import Game, League, Team, Player as YfpyPlayer, Roster, Matchup

from config.settings import Settings
from ..models.player import (
    Player,
    Position,
    Team as NFLTeam,
    InjuryReport,
    InjuryStatus,
    PlayerStats,
)
from ..models.matchup import Matchup as FantasyMatchup, GameStatus
from ..models.lineup import Lineup
from .cache_manager import CacheManagerAgent

# Find project root (where .env file is located)
# This file is in src/agents/, so project root is 2 levels up
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class APIEndpoint(str, Enum):
    """Yahoo Fantasy Sports API endpoints."""

    USER_LEAGUES = "user_leagues"
    LEAGUE_INFO = "league_info"
    TEAM_ROSTER = "team_roster"
    TEAM_MATCHUP = "team_matchup"
    PLAYER_INFO = "player_info"
    AVAILABLE_PLAYERS = "available_players"
    INJURY_REPORT = "injury_report"
    LEAGUE_STANDINGS = "league_standings"
    LEAGUE_TRANSACTIONS = "league_transactions"


class RateLimitError(Exception):
    """Exception raised when API rate limit is exceeded."""

    pass


class AuthenticationError(Exception):
    """Exception raised when authentication fails."""

    pass


@dataclass
class APIRequest:
    """API request wrapper with retry logic."""

    endpoint: APIEndpoint
    params: Dict[str, Any]
    attempt: int = 0
    max_retries: int = 3
    backoff_factor: float = 2.0
    timeout: int = 30


@dataclass
class RateLimitTracker:
    """Track API rate limiting."""

    requests_per_window: int = 100
    window_seconds: int = 3600
    requests_made: int = 0
    window_start: datetime = None

    def __post_init__(self):
        if self.window_start is None:
            self.window_start = datetime.utcnow()

    def can_make_request(self) -> bool:
        """Check if we can make another request within rate limits."""
        now = datetime.utcnow()

        # Reset window if expired
        if now - self.window_start > timedelta(seconds=self.window_seconds):
            self.requests_made = 0
            self.window_start = now

        return self.requests_made < self.requests_per_window

    def record_request(self) -> None:
        """Record a successful API request."""
        self.requests_made += 1

    def time_until_reset(self) -> timedelta:
        """Get time until rate limit window resets."""
        window_end = self.window_start + timedelta(seconds=self.window_seconds)
        remaining = window_end - datetime.utcnow()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)


class DataFetcherAgent:
    """
    Agent responsible for fetching data from Yahoo Fantasy Sports API.

    This agent handles:
    - OAuth2 authentication with Yahoo
    - Rate-limited API requests with retry logic
    - Parallel data fetching for multiple leagues
    - Intelligent caching of API responses
    - Data transformation to internal models
    - Graceful error handling and recovery
    """

    def __init__(self, settings: Settings, cache_manager: CacheManagerAgent):
        """
        Initialize the data fetcher agent.

        Args:
            settings: Application settings containing API configuration
            cache_manager: Cache manager for intelligent caching
        """
        self.settings = settings
        self.cache_manager = cache_manager

        # Rate limiting
        self.rate_limiter = RateLimitTracker(
            requests_per_window=settings.yahoo_api_rate_limit,
            window_seconds=settings.yahoo_api_rate_window_seconds,
        )

        # Yahoo API client (initialized on first use)
        self._yahoo_client: Optional[YahooFantasySportsQuery] = None
        self._auth_token: Optional[str] = None
        self._auth_expires: Optional[datetime] = None

        # Session for HTTP requests
        self._session: Optional[aiohttp.ClientSession] = None

        # Semaphore for controlling concurrent requests
        self._semaphore = asyncio.Semaphore(settings.max_workers)

        logger.info("DataFetcherAgent initialized")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()

    async def initialize(self) -> None:
        """Initialize the data fetcher."""
        try:
            # Create HTTP session
            timeout = aiohttp.ClientTimeout(total=self.settings.async_timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)

            # Initialize Yahoo API client
            await self._initialize_yahoo_client()

            logger.info("DataFetcherAgent initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize DataFetcherAgent: {e}")
            raise

    async def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if self._session:
                await self._session.close()

            logger.info("DataFetcherAgent cleaned up")

        except Exception as e:
            logger.error(f"Error during DataFetcherAgent cleanup: {e}")

    async def get_user_leagues(self, game_key: str = None) -> List[Dict[str, Any]]:
        """
        Get all leagues for the authenticated user.

        Args:
            game_key: Optional specific game/season (e.g., "nfl.2024")

        Returns:
            List of league information dictionaries
        """
        cache_key = f"user_leagues:{game_key or 'all'}"

        # Check cache first
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Returning cached user leagues for game: {game_key}")
            return cached_data

        try:
            # Make API request
            request = APIRequest(
                endpoint=APIEndpoint.USER_LEAGUES, params={"game_key": game_key} if game_key else {}
            )

            leagues_data = await self._make_api_request(request)

            # Transform to our format
            leagues = []
            if leagues_data and hasattr(leagues_data, "leagues"):
                for league in leagues_data.leagues:
                    league_info = {
                        "league_id": league.league_id,
                        "league_key": league.league_key,
                        "name": league.name,
                        "game_key": league.game_key,
                        "season": league.season,
                        "is_finished": getattr(league, "is_finished", False),
                        "num_teams": getattr(league, "num_teams", None),
                        "scoring_type": getattr(league, "scoring_type", None),
                        "league_type": getattr(league, "league_type", None),
                        "url": getattr(league, "url", None),
                    }
                    leagues.append(league_info)

            # Cache the results
            await self.cache_manager.set(
                cache_key,
                leagues,
                ttl=timedelta(hours=4),  # Leagues don't change often
                tags=["user_leagues", "yahoo_api"],
            )

            logger.info(f"Retrieved {len(leagues)} leagues for user")
            return leagues

        except Exception as e:
            logger.error(f"Error getting user leagues: {e}")
            raise

    async def get_roster(self, league_key: str, team_key: str, week: int = None) -> Dict[str, Any]:
        """
        Get team roster for a specific league and week.

        Args:
            league_key: Yahoo league identifier
            team_key: Yahoo team identifier
            week: Optional week number (current week if not specified)

        Returns:
            Roster information dictionary
        """
        cache_key = f"roster:{league_key}:{team_key}:{week or 'current'}"

        # Check cache first
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Returning cached roster for team {team_key}, week {week}")
            return cached_data

        try:
            # Make API request
            request = APIRequest(
                endpoint=APIEndpoint.TEAM_ROSTER,
                params={"league_key": league_key, "team_key": team_key, "week": week},
            )

            roster_data = await self._make_api_request(request)

            # Transform roster data
            roster_info = {
                "team_key": team_key,
                "league_key": league_key,
                "week": week,
                "players": [],
                "last_updated": datetime.utcnow().isoformat(),
            }

            if roster_data and hasattr(roster_data, "players"):
                for player in roster_data.players:
                    player_info = await self._transform_yahoo_player(player)
                    roster_info["players"].append(player_info)

            # Cache with shorter TTL since rosters change frequently
            await self.cache_manager.set(
                cache_key,
                roster_info,
                ttl=timedelta(hours=2),
                tags=["roster", "yahoo_api", f"league:{league_key}"],
            )

            logger.info(
                f"Retrieved roster for team {team_key}, {len(roster_info['players'])} players"
            )
            return roster_info

        except Exception as e:
            logger.error(f"Error getting roster for team {team_key}: {e}")
            raise

    async def get_matchup(self, league_key: str, team_key: str, week: int) -> Dict[str, Any]:
        """
        Get matchup information for a team in a specific week.

        Args:
            league_key: Yahoo league identifier
            team_key: Yahoo team identifier
            week: Week number

        Returns:
            Matchup information dictionary
        """
        cache_key = f"matchup:{league_key}:{team_key}:{week}"

        # Check cache first
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Returning cached matchup for team {team_key}, week {week}")
            return cached_data

        try:
            # Make API request
            request = APIRequest(
                endpoint=APIEndpoint.TEAM_MATCHUP,
                params={"league_key": league_key, "team_key": team_key, "week": week},
            )

            matchup_data = await self._make_api_request(request)

            # Transform matchup data
            matchup_info = {
                "league_key": league_key,
                "week": week,
                "teams": [],
                "is_playoffs": False,
                "is_consolation": False,
                "winner_team_key": None,
                "status": "upcoming",
                "last_updated": datetime.utcnow().isoformat(),
            }

            if matchup_data and hasattr(matchup_data, "teams"):
                for team in matchup_data.teams:
                    team_info = {
                        "team_key": team.team_key,
                        "name": getattr(team, "name", ""),
                        "projected_points": getattr(team, "projected_points", None),
                        "actual_points": getattr(team, "actual_points", None),
                    }
                    matchup_info["teams"].append(team_info)

            # Set matchup status and winner if available
            if hasattr(matchup_data, "status"):
                matchup_info["status"] = matchup_data.status
            if hasattr(matchup_data, "winner_team_key"):
                matchup_info["winner_team_key"] = matchup_data.winner_team_key

            # Cache matchup data
            await self.cache_manager.set(
                cache_key,
                matchup_info,
                ttl=timedelta(hours=1),  # Matchups update during games
                tags=["matchup", "yahoo_api", f"league:{league_key}", f"week:{week}"],
            )

            logger.info(f"Retrieved matchup for team {team_key}, week {week}")
            return matchup_info

        except Exception as e:
            logger.error(f"Error getting matchup for team {team_key}, week {week}: {e}")
            raise

    async def get_player(self, player_key: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific player.

        Args:
            player_key: Yahoo player identifier

        Returns:
            Player information dictionary or None if not found
        """
        cache_key = f"player:{player_key}"

        # Check cache first
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Returning cached player data for {player_key}")
            return cached_data

        try:
            # Make API request
            request = APIRequest(
                endpoint=APIEndpoint.PLAYER_INFO, params={"player_key": player_key}
            )

            player_data = await self._make_api_request(request)

            if not player_data:
                return None

            # Transform player data
            player_info = await self._transform_yahoo_player(player_data)

            # Cache player data with longer TTL (player info doesn't change much)
            await self.cache_manager.set(
                cache_key, player_info, ttl=timedelta(hours=6), tags=["player", "yahoo_api"]
            )

            logger.debug(f"Retrieved player data for {player_key}")
            return player_info

        except Exception as e:
            logger.error(f"Error getting player {player_key}: {e}")
            return None

    async def get_available_players(
        self,
        league_key: str,
        position: str = None,
        status: str = "A",  # A=Available, W=Waivers, T=Taken
        count: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Get available players in a league.

        Args:
            league_key: Yahoo league identifier
            position: Optional position filter (QB, RB, WR, TE, K, DEF)
            status: Player status filter (A=Available, W=Waivers, T=Taken)
            count: Maximum number of players to return

        Returns:
            List of available player information dictionaries
        """
        cache_key = f"available_players:{league_key}:{position or 'all'}:{status}:{count}"

        # Check cache first (shorter TTL since availability changes frequently)
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Returning cached available players for league {league_key}")
            return cached_data

        try:
            # Make API request
            request = APIRequest(
                endpoint=APIEndpoint.AVAILABLE_PLAYERS,
                params={
                    "league_key": league_key,
                    "position": position,
                    "status": status,
                    "count": count,
                },
            )

            players_data = await self._make_api_request(request)

            # Transform players data
            available_players = []
            if players_data and hasattr(players_data, "players"):
                for player in players_data.players:
                    player_info = await self._transform_yahoo_player(player)
                    available_players.append(player_info)

            # Cache with short TTL since player availability changes rapidly
            await self.cache_manager.set(
                cache_key,
                available_players,
                ttl=timedelta(minutes=30),
                tags=["available_players", "yahoo_api", f"league:{league_key}"],
            )

            logger.info(
                f"Retrieved {len(available_players)} available players for league {league_key}"
            )
            return available_players

        except Exception as e:
            logger.error(f"Error getting available players for league {league_key}: {e}")
            raise

    async def get_injury_report(self, league_key: str = None) -> List[Dict[str, Any]]:
        """
        Get current injury report for players.

        Args:
            league_key: Optional league context for relevant players

        Returns:
            List of injury report dictionaries
        """
        cache_key = f"injury_report:{league_key or 'all'}"

        # Check cache first
        cached_data = await self.cache_manager.get(cache_key)
        if cached_data is not None:
            logger.debug("Returning cached injury report")
            return cached_data

        try:
            # This would typically call a specialized injury report endpoint
            # For now, we'll get it through available players with injury status
            available_players = await self.get_available_players(
                league_key, status="A", count=500  # All players to check injury status
            )

            # Filter for injured players
            injured_players = []
            for player in available_players:
                if player.get("injury_status") and player["injury_status"] != "Healthy":
                    injury_info = {
                        "player_key": player["player_key"],
                        "player_name": player["name"],
                        "team": player.get("team"),
                        "position": player.get("position"),
                        "injury_status": player["injury_status"],
                        "injury_note": player.get("injury_note", ""),
                        "last_updated": datetime.utcnow().isoformat(),
                    }
                    injured_players.append(injury_info)

            # Cache injury report with medium TTL
            await self.cache_manager.set(
                cache_key,
                injured_players,
                ttl=timedelta(hours=2),
                tags=["injury_report", "yahoo_api"],
            )

            logger.info(f"Retrieved injury report with {len(injured_players)} injured players")
            return injured_players

        except Exception as e:
            logger.error(f"Error getting injury report: {e}")
            raise

    async def fetch_multiple_leagues_data(
        self, league_keys: List[str], data_types: List[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch data for multiple leagues in parallel.

        Args:
            league_keys: List of Yahoo league identifiers
            data_types: List of data types to fetch (roster, matchup, standings, etc.)

        Returns:
            Dictionary mapping league_key to fetched data
        """
        if data_types is None:
            data_types = ["roster", "standings"]

        logger.info(f"Fetching data for {len(league_keys)} leagues in parallel")

        # Create tasks for parallel execution
        tasks = []
        for league_key in league_keys:
            task = asyncio.create_task(
                self._fetch_league_data(league_key, data_types), name=f"fetch_league_{league_key}"
            )
            tasks.append(task)

        # Execute tasks with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.settings.async_timeout_seconds * len(league_keys),
            )

            # Process results
            league_data = {}
            for i, result in enumerate(results):
                league_key = league_keys[i]
                if isinstance(result, Exception):
                    logger.error(f"Error fetching data for league {league_key}: {result}")
                    league_data[league_key] = {"error": str(result)}
                else:
                    league_data[league_key] = result

            logger.info(f"Completed parallel fetch for {len(league_keys)} leagues")
            return league_data

        except asyncio.TimeoutError:
            logger.error("Timeout while fetching multiple leagues data")
            raise
        except Exception as e:
            logger.error(f"Error in parallel league data fetch: {e}")
            raise

    async def _fetch_league_data(self, league_key: str, data_types: List[str]) -> Dict[str, Any]:
        """Fetch specific data types for a single league."""
        league_data = {"league_key": league_key}

        # Fetch each requested data type
        for data_type in data_types:
            try:
                if data_type == "roster":
                    # Get roster for the user's team (assuming first team)
                    # This would need team identification logic in a real implementation
                    pass
                elif data_type == "standings":
                    # Implementation for standings
                    pass
                elif data_type == "available_players":
                    league_data["available_players"] = await self.get_available_players(league_key)

            except Exception as e:
                logger.error(f"Error fetching {data_type} for league {league_key}: {e}")
                league_data[data_type] = {"error": str(e)}

        return league_data

    async def _initialize_yahoo_client(self) -> None:
        """Initialize Yahoo Fantasy Sports API client."""
        try:
            # Create Yahoo API client with OAuth2 credentials
            self._yahoo_client = YahooFantasySportsQuery(
                league_id=None,  # Will be set per request
                game_code="nfl",
                game_id=None,  # Will be determined from current season
                YAHOO_CLIENT_ID=self.settings.yahoo_client_id,
                YAHOO_CLIENT_SECRET=self.settings.yahoo_client_secret,
                env_file_location=str(ENV_FILE_PATH),  # OAuth tokens stored in project root .env
            )

            logger.info("Yahoo API client initialized")

        except Exception as e:
            logger.error(f"Failed to initialize Yahoo API client: {e}")
            raise AuthenticationError(f"Yahoo API authentication failed: {e}")

    async def _make_api_request(self, request: APIRequest) -> Any:
        """
        Make API request with rate limiting, retry logic, and error handling.

        Args:
            request: API request configuration

        Returns:
            API response data
        """
        async with self._semaphore:
            # Check rate limits
            if not self.rate_limiter.can_make_request():
                wait_time = self.rate_limiter.time_until_reset().total_seconds()
                logger.warning(f"Rate limit exceeded, waiting {wait_time} seconds")
                if wait_time > 0:
                    await asyncio.sleep(min(wait_time, 300))  # Max 5 minute wait

                if not self.rate_limiter.can_make_request():
                    raise RateLimitError("API rate limit exceeded")

            # Retry logic
            last_exception = None
            for attempt in range(request.max_retries + 1):
                try:
                    # Calculate backoff delay
                    if attempt > 0:
                        delay = request.backoff_factor**attempt
                        logger.debug(
                            f"Retrying request after {delay}s delay (attempt {attempt + 1})"
                        )
                        await asyncio.sleep(delay)

                    # Make the actual API call
                    response = await self._execute_yahoo_request(request)

                    # Record successful request
                    self.rate_limiter.record_request()

                    logger.debug(f"API request successful: {request.endpoint}")
                    return response

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exception = e
                    logger.warning(f"API request failed (attempt {attempt + 1}): {e}")

                    if attempt == request.max_retries:
                        break

                except RateLimitError:
                    # Don't retry rate limit errors immediately
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error in API request: {e}")
                    raise

            # All retries exhausted
            logger.error(f"API request failed after {request.max_retries + 1} attempts")
            raise last_exception or Exception("API request failed")

    async def _execute_yahoo_request(self, request: APIRequest) -> Any:
        """Execute the actual Yahoo API request."""
        try:
            if not self._yahoo_client:
                await self._initialize_yahoo_client()

            # Route to appropriate Yahoo API method
            if request.endpoint == APIEndpoint.USER_LEAGUES:
                return self._yahoo_client.get_user_leagues()

            elif request.endpoint == APIEndpoint.TEAM_ROSTER:
                league_key = request.params["league_key"]
                team_key = request.params["team_key"]
                week = request.params.get("week")

                # Set league context
                self._yahoo_client.league_id = league_key.split(".")[-1]

                if week:
                    return self._yahoo_client.get_team_roster_player_info_by_week(
                        team_id=team_key.split(".")[-1], chosen_week=week
                    )
                else:
                    return self._yahoo_client.get_team_roster_player_info(
                        team_id=team_key.split(".")[-1]
                    )

            elif request.endpoint == APIEndpoint.TEAM_MATCHUP:
                league_key = request.params["league_key"]
                team_key = request.params["team_key"]
                week = request.params["week"]

                self._yahoo_client.league_id = league_key.split(".")[-1]
                return self._yahoo_client.get_team_matchups(
                    team_id=team_key.split(".")[-1], chosen_week=week
                )

            elif request.endpoint == APIEndpoint.PLAYER_INFO:
                player_key = request.params["player_key"]
                return self._yahoo_client.get_player_info(player_key)

            elif request.endpoint == APIEndpoint.AVAILABLE_PLAYERS:
                league_key = request.params["league_key"]
                self._yahoo_client.league_id = league_key.split(".")[-1]

                return self._yahoo_client.get_league_players(
                    player_count_limit=request.params.get("count", 25),
                    player_count_start=0,
                    search_filters={
                        "position": request.params.get("position"),
                        "status": request.params.get("status", "A"),
                    },
                )

            else:
                raise ValueError(f"Unsupported endpoint: {request.endpoint}")

        except Exception as e:
            logger.error(f"Yahoo API request execution failed: {e}")
            raise

    async def _transform_yahoo_player(self, yahoo_player: YfpyPlayer) -> Dict[str, Any]:
        """
        Transform Yahoo player object to our internal format.

        Args:
            yahoo_player: Yahoo API player object

        Returns:
            Player information dictionary in our format
        """
        try:
            # Map Yahoo position to our Position enum
            position_map = {
                "QB": Position.QB,
                "RB": Position.RB,
                "WR": Position.WR,
                "TE": Position.TE,
                "K": Position.K,
                "DEF": Position.DEF,
            }

            # Map Yahoo team to our Team enum
            yahoo_team = getattr(yahoo_player, "editorial_team_abbr", "") or getattr(
                yahoo_player, "team_abbr", ""
            )
            nfl_team = None
            try:
                nfl_team = NFLTeam(yahoo_team.upper()) if yahoo_team else None
            except ValueError:
                logger.warning(f"Unknown NFL team: {yahoo_team}")

            # Basic player information
            player_info = {
                "player_key": yahoo_player.player_key,
                "name": f"{yahoo_player.name.first} {yahoo_player.name.last}",
                "position": getattr(yahoo_player, "primary_position", ""),
                "team": yahoo_team,
                "jersey_number": getattr(yahoo_player, "jersey_number", None),
                "bye_weeks": getattr(yahoo_player, "bye_weeks", []),
                "is_undroppable": getattr(yahoo_player, "is_undroppable", False),
                "ownership_status": getattr(yahoo_player, "ownership", {}).get(
                    "ownership_type", "available"
                ),
                "percent_owned": getattr(yahoo_player, "percent_owned", None),
            }

            # Injury information
            if hasattr(yahoo_player, "status") and yahoo_player.status:
                player_info["injury_status"] = yahoo_player.status
            else:
                player_info["injury_status"] = "Healthy"

            if hasattr(yahoo_player, "injury_note"):
                player_info["injury_note"] = yahoo_player.injury_note

            # Statistics if available
            if hasattr(yahoo_player, "player_stats") and yahoo_player.player_stats:
                stats = {}
                for stat in yahoo_player.player_stats.stats:
                    stats[stat.stat.display_name] = stat.value
                player_info["stats"] = stats

            # Projected points if available
            if hasattr(yahoo_player, "player_points") and yahoo_player.player_points:
                player_info["projected_points"] = yahoo_player.player_points.total

            return player_info

        except Exception as e:
            logger.error(f"Error transforming Yahoo player data: {e}")
            # Return minimal player info if transformation fails
            return {
                "player_key": getattr(yahoo_player, "player_key", ""),
                "name": "Unknown Player",
                "position": "",
                "team": "",
                "error": str(e),
            }

    def _generate_cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Generate consistent cache key from endpoint and parameters."""
        # Sort parameters for consistent key generation
        sorted_params = sorted(params.items())
        param_string = "&".join([f"{k}={v}" for k, v in sorted_params])

        # Create hash of the full request
        full_string = f"{endpoint}?{param_string}"
        return hashlib.md5(full_string.encode()).hexdigest()
