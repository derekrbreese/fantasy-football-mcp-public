"""
Tests for bye week utility module with static data fallback.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from src.utils.bye_weeks import (
    load_static_bye_weeks,
    get_bye_week_with_fallback,
    build_team_bye_week_map,
    clear_cache,
)


@pytest.fixture(autouse=True)
def clear_bye_week_cache():
    """Clear the bye week cache before each test."""
    clear_cache()
    yield
    clear_cache()


class TestLoadStaticByeWeeks:
    """Test loading static bye week data from JSON file."""

    def test_load_static_bye_weeks_success(self):
        """Test successful loading of static bye week data."""
        result = load_static_bye_weeks()
        
        assert isinstance(result, dict)
        assert len(result) == 32  # All 32 NFL teams
        
        # Check a few specific teams
        assert result["KC"] == 10  # Kansas City Chiefs
        assert result["SF"] == 14  # San Francisco 49ers
        assert result["BUF"] == 7  # Buffalo Bills
        assert result["DAL"] == 10  # Dallas Cowboys
        
        # Verify all values are valid week numbers
        for team, week in result.items():
            assert isinstance(week, int)
            assert 1 <= week <= 18

    def test_load_static_bye_weeks_caching(self):
        """Test that static data is cached after first load."""
        # First call loads from file
        result1 = load_static_bye_weeks()
        
        # Second call should return cached data (same object)
        result2 = load_static_bye_weeks()
        
        assert result1 is result2  # Same object reference

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_load_static_bye_weeks_file_not_found(self, mock_file):
        """Test handling when static data file is not found."""
        clear_cache()  # Clear cache to force file read
        result = load_static_bye_weeks()
        
        assert result == {}  # Returns empty dict on error

    @patch("builtins.open", mock_open(read_data="invalid json"))
    def test_load_static_bye_weeks_invalid_json(self):
        """Test handling of invalid JSON in data file."""
        clear_cache()
        result = load_static_bye_weeks()
        
        assert result == {}  # Returns empty dict on parse error

    @patch("builtins.open", mock_open(read_data='["not", "a", "dict"]'))
    def test_load_static_bye_weeks_wrong_format(self):
        """Test handling when data is not a dictionary."""
        clear_cache()
        result = load_static_bye_weeks()
        
        assert result == {}  # Returns empty dict when format is wrong


class TestGetByeWeekWithFallback:
    """Test bye week retrieval: Yahoo's value first, the static file as a fallback."""

    def test_get_bye_week_api_overrides_static(self):
        """A valid API value wins: the static file is one season's schedule."""
        result = get_bye_week_with_fallback("KC", api_bye_week=6)
        assert result == 6  # API value, not static KC=10

        result = get_bye_week_with_fallback("KC", api_bye_week=10)
        assert result == 10

    def test_get_bye_week_invalid_api_data_uses_static(self):
        """Test using static data when API data is invalid."""
        # Invalid: week 0
        result = get_bye_week_with_fallback("KC", api_bye_week=0)
        assert result == 10  # Uses static data
        
        # Invalid: week 19
        result = get_bye_week_with_fallback("SF", api_bye_week=19)
        assert result == 14  # Uses static data
        
        # Invalid: negative week
        result = get_bye_week_with_fallback("BUF", api_bye_week=-1)
        assert result == 7  # Uses static data

    def test_get_bye_week_no_api_data_uses_static(self):
        """Test using static data when API data is not provided."""
        result = get_bye_week_with_fallback("DAL", api_bye_week=None)
        assert result == 10  # Uses static data
        
        result = get_bye_week_with_fallback("LAR")
        assert result == 8  # Uses static data (default None)

    def test_get_bye_week_nonexistent_team(self):
        """Test handling of unknown team abbreviation with no API fallback."""
        result = get_bye_week_with_fallback("XXX", api_bye_week=None)
        assert result is None  # Returns None for unknown teams
        
        # With valid API data but unknown team, use API as fallback
        result = get_bye_week_with_fallback("XXX", api_bye_week=7)
        assert result == 7  # Uses API data when team not in static

    def test_get_bye_week_real_2026_values_beat_2025_file(self):
        """Yahoo's 2026 byes differ from the 2025 file for most teams; Yahoo's must be used."""
        assert get_bye_week_with_fallback("SF", api_bye_week=8) == 8  # file says 14
        assert get_bye_week_with_fallback("GB", api_bye_week=11) == 11  # file says 5
        # True is an int in Python; it is not a week number.
        assert get_bye_week_with_fallback("SF", api_bye_week=True) == 14

    def test_get_bye_week_all_teams_have_static_data(self):
        """Test that all 32 NFL teams have static data."""
        teams = [
            "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
            "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAC", "KC",
            "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
            "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"
        ]
        
        for team in teams:
            result = get_bye_week_with_fallback(team, api_bye_week=None)
            assert result is not None
            assert 1 <= result <= 18


class TestBuildTeamByeWeekMap:
    """Test building complete team-to-bye-week mapping."""

    def test_build_map_with_no_api_data(self):
        """Test building map using only static data."""
        result = build_team_bye_week_map()
        
        assert isinstance(result, dict)
        assert len(result) == 32
        assert result["KC"] == 10
        assert result["SF"] == 14

    def test_build_map_with_valid_api_data(self):
        """Test building map with API data overriding static data."""
        api_data = {
            "KC": 8,  # Override static (10)
            "SF": 12,  # Override static (14)
            "BUF": 7,  # Same as static
        }
        
        result = build_team_bye_week_map(api_data)
        
        # API data should override static
        assert result["KC"] == 8
        assert result["SF"] == 12
        assert result["BUF"] == 7
        
        # Other teams should still use static data
        assert result["DAL"] == 10
        assert result["LAR"] == 8

    def test_build_map_filters_invalid_api_data(self):
        """Test that invalid API data is filtered out."""
        api_data = {
            "KC": 8,  # Valid
            "SF": 0,  # Invalid (too low)
            "BUF": 19,  # Invalid (too high)
            "DAL": "invalid",  # Invalid (not int)
        }
        
        result = build_team_bye_week_map(api_data)
        
        # Valid API data used
        assert result["KC"] == 8
        
        # Invalid API data ignored, static data used
        assert result["SF"] == 14  # Static
        assert result["BUF"] == 7  # Static
        assert result["DAL"] == 10  # Static

    def test_build_map_preserves_all_teams(self):
        """Test that the map includes all teams even with partial API data."""
        api_data = {
            "KC": 6,
            "SF": 9,
        }
        
        result = build_team_bye_week_map(api_data)
        
        # Should still have all 32 teams
        assert len(result) == 32


class TestCacheManagement:
    """Test cache clearing functionality."""

    def test_clear_cache_reloads_data(self):
        """Test that clearing cache forces data reload."""
        # Load data (will be cached)
        data1 = load_static_bye_weeks()
        
        # Clear cache
        clear_cache()
        
        # Load again (should reload from file)
        data2 = load_static_bye_weeks()
        
        # Data should be equal but different objects
        assert data1 == data2
        # Note: We can't easily test that it's a different object
        # without mocking the file system


class TestIntegrationScenarios:
    """Test real-world integration scenarios."""

    def test_missing_api_data_scenario(self):
        """Test scenario where Yahoo API doesn't provide bye weeks."""
        # Simulate multiple players with missing API data
        players = [
            {"team": "KC", "api_bye": None},
            {"team": "SF", "api_bye": None},
            {"team": "BUF", "api_bye": None},
        ]
        
        for player in players:
            bye = get_bye_week_with_fallback(player["team"], player["api_bye"])
            assert bye is not None
            assert 1 <= bye <= 18

    def test_mixed_api_quality_scenario(self):
        """Test scenario with mix of valid, invalid, and missing API data."""
        test_cases = [
            ("KC", 10, 10),  # Valid API data
            ("SF", 0, 14),  # Invalid API (0), use static
            ("BUF", None, 7),  # Missing API, use static
            ("DAL", 99, 10),  # Invalid API (99), use static
        ]
        
        for team, api_bye, expected in test_cases:
            result = get_bye_week_with_fallback(team, api_bye)
            assert result == expected

    def test_season_update_scenario(self):
        """Test that static data can be updated for new season."""
        # This test verifies the structure supports updates
        static_data = load_static_bye_weeks()
        
        # Verify format allows easy updates
        assert isinstance(static_data, dict)
        assert all(isinstance(k, str) for k in static_data.keys())
        assert all(isinstance(v, int) for v in static_data.values())