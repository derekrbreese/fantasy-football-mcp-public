"""Unit tests for lineup_optimizer.py - Lineup optimization logic."""

import pytest

from lineup_optimizer import (
    BENCH_SLOTS,
    MATCH_CONFIDENCE,
    LineupOptimizer,
    MatchAnalytics,
    Player,
    _calculate_dynamic_weights,
    _calculate_match_confidence,
    _coerce_float,
    _coerce_int,
    _normalize_position,
)


class TestUtilityFunctions:
    """Test utility helper functions."""

    def test_coerce_float_valid_number(self):
        """Test coercing valid numbers to float."""
        assert _coerce_float(42) == 42.0
        assert _coerce_float(3.14) == 3.14
        assert _coerce_float("10.5") == 10.5
        assert _coerce_float("100") == 100.0

    def test_coerce_float_invalid(self):
        """Test coercing invalid values returns 0.0."""
        assert _coerce_float("abc") == 0.0
        assert _coerce_float(None) == 0.0
        assert _coerce_float("") == 0.0
        assert _coerce_float({}) == 0.0

    def test_coerce_int_valid_number(self):
        """Test coercing valid numbers to int."""
        assert _coerce_int(42) == 42
        assert _coerce_int(3.14) == 3
        assert _coerce_int("10") == 10
        assert _coerce_int(99.9) == 99

    def test_coerce_int_invalid(self):
        """Test coercing invalid values returns default."""
        assert _coerce_int("abc") == 0
        assert _coerce_int("abc", default=5) == 5
        assert _coerce_int(None) == 0
        assert _coerce_int(None, default=-1) == -1

    def test_normalize_position_valid(self):
        """Test normalizing valid position values."""
        assert _normalize_position("QB") == "QB"
        assert _normalize_position("RB") == "RB"
        assert _normalize_position("WR") == "WR"
        assert _normalize_position("TE") == "TE"
        assert _normalize_position("K") == "K"
        assert _normalize_position("DEF") == "DEF"

    def test_normalize_position_variations(self):
        """Test normalizing position variations."""
        # normalize_position just uppercases, doesn't transform positions
        assert _normalize_position("D/ST") == "D/ST"
        assert _normalize_position("dst") == "DST"
        assert _normalize_position("ir") == "IR"
        assert _normalize_position("qb") == "QB"

    def test_normalize_position_dict(self):
        """Test normalizing position from dict structure."""
        assert _normalize_position({"position": "QB"}) == "QB"
        assert _normalize_position({"0": {"position": "RB"}}) == "RB"

    def test_normalize_position_list(self):
        """Test normalizing position from list structure."""
        # Lists are converted to string and uppercased
        assert _normalize_position([{"position": "WR"}]) == "[{'POSITION': 'WR'}]"

    def test_normalize_position_invalid(self):
        """Test normalizing invalid position returns BN or converts to string."""
        assert _normalize_position(None) == "BN"  # None/empty returns BN
        assert _normalize_position("") == "BN"  # Empty returns BN
        assert _normalize_position(123) == "123"  # Numbers converted to string

    def test_calculate_match_confidence(self):
        """Test match confidence calculation."""
        assert _calculate_match_confidence("exact") == 1.0
        assert _calculate_match_confidence("normalized") == 0.9
        assert _calculate_match_confidence("fuzzy") == 0.4
        assert _calculate_match_confidence("failed") == 0.0
        assert _calculate_match_confidence("exact_pos_mismatch") == 0.5  # 1.0 * 0.5 penalty
        assert _calculate_match_confidence("normalized_team_mismatch") == 0.45  # 0.9 * 0.5 penalty


class TestMatchAnalytics:
    """Test match analytics tracking."""

    def test_match_analytics_initialization(self):
        """Test analytics starts with zero counts."""
        analytics = MatchAnalytics()
        assert analytics.total_players == 0
        assert analytics.matched_players == 0
        assert analytics.failed_matches == 0

    def test_add_match_exact(self):
        """Test adding exact match."""
        analytics = MatchAnalytics()
        analytics.add_match("exact", 1.0)

        assert analytics.total_players == 1
        assert analytics.matched_players == 1
        assert analytics.exact_matches == 1
        assert analytics.avg_match_confidence == 1.0

    def test_add_match_failed(self):
        """Test adding failed match."""
        analytics = MatchAnalytics()
        analytics.add_match("failed", 0.0)

        assert analytics.total_players == 1
        assert analytics.matched_players == 0
        assert analytics.failed_matches == 1

    def test_add_match_with_mismatches(self):
        """Test adding match with position/team mismatches."""
        analytics = MatchAnalytics()
        analytics.add_match("exact_pos_mismatch", 0.5)

        assert analytics.position_mismatches == 1
        assert analytics.exact_matches == 1

        analytics.add_match("normalized_team_mismatch", 0.45)
        assert analytics.team_mismatches == 1

    def test_get_success_rate(self):
        """Test calculating success rate."""
        analytics = MatchAnalytics()
        assert analytics.get_success_rate() == 0.0  # No players

        analytics.add_match("exact", 1.0)
        analytics.add_match("exact", 1.0)
        analytics.add_match("failed", 0.0)
        assert analytics.get_success_rate() == 2 / 3  # 2 matched out of 3

    def test_average_confidence_calculation(self):
        """Test average confidence across multiple matches."""
        analytics = MatchAnalytics()
        analytics.add_match("exact", 1.0)
        analytics.add_match("normalized", 0.9)
        analytics.add_match("fuzzy", 0.4)

        expected_avg = (1.0 + 0.9 + 0.4) / 3
        assert abs(analytics.avg_match_confidence - expected_avg) < 0.01


class TestPlayer:
    """Test Player dataclass."""

    def test_player_creation(self):
        """Test creating a basic player."""
        player = Player(
            name="Josh Allen",
            position="QB",
            team="BUF",
            yahoo_projection=24.5,
        )

        assert player.name == "Josh Allen"
        assert player.position == "QB"
        assert player.team == "BUF"
        assert player.yahoo_projection == 24.5
        assert player.status == "OK"  # Default value

    def test_player_is_valid(self):
        """Test player validity check."""
        valid_player = Player(name="Josh Allen", position="QB", team="BUF")
        assert valid_player.is_valid()

        invalid_player = Player(name="", position="QB", team="BUF")
        assert not invalid_player.is_valid()

        invalid_player2 = Player(name="Josh Allen", position="QB", team="")
        assert not invalid_player2.is_valid()

    def test_player_default_values(self):
        """Test player default field values."""
        player = Player(name="Test Player", position="RB", team="KC")

        assert player.opponent == ""
        assert player.yahoo_projection == 0.0
        assert player.sleeper_projection == 0.0
        assert player.matchup_score == 50
        assert player.injury_probability == 0.0
        assert player.recent_performance == []
        assert player.composite_score == 0.0


class TestLineupOptimizer:
    """Test LineupOptimizer functionality."""

    @pytest.mark.asyncio
    async def test_parse_yahoo_roster_basic(self):
        """Test parsing basic roster payload."""
        optimizer = LineupOptimizer()
        roster_payload = {
            "roster": [
                {
                    "name": "Josh Allen",
                    "position": "QB",
                    "team": "BUF",
                    "status": "OK",
                },
                {
                    "name": "Christian McCaffrey",
                    "position": "RB",
                    "team": "SF",
                    "status": "O",
                },
            ]
        }

        players = await optimizer.parse_yahoo_roster(roster_payload)

        assert len(players) == 2
        assert players[0].name == "Josh Allen"
        assert players[0].position == "QB"
        assert players[0].team == "BUF"

        assert players[1].name == "Christian McCaffrey"
        assert players[1].position == "RB"
        assert players[1].status == "O"

    @pytest.mark.asyncio
    async def test_parse_yahoo_roster_empty(self):
        """Test parsing empty roster."""
        optimizer = LineupOptimizer()
        roster_payload = {"roster": []}

        players = await optimizer.parse_yahoo_roster(roster_payload)
        assert players == []

    @pytest.mark.asyncio
    async def test_parse_yahoo_roster_filters_invalid(self):
        """Test that invalid players are filtered out."""
        optimizer = LineupOptimizer()
        roster_payload = {
            "roster": [
                {"name": "Valid Player", "position": "QB", "team": "BUF"},
                {"name": "", "position": "RB", "team": "KC"},  # No name
                {"position": "WR", "team": "LAR"},  # No name
            ]
        }

        players = await optimizer.parse_yahoo_roster(roster_payload)
        assert len(players) == 1
        assert players[0].name == "Valid Player"

    @pytest.mark.asyncio
    async def test_parse_yahoo_roster_normalizes_positions(self):
        """Test that positions are uppercased during parsing."""
        optimizer = LineupOptimizer()
        roster_payload = {
            "roster": [
                {"name": "DEF Player", "position": "d/st", "team": "SF"},
                {"name": "Bench Player", "position": "bn", "team": "KC"},
            ]
        }

        players = await optimizer.parse_yahoo_roster(roster_payload)

        assert players[0].position == "D/ST"  # Uppercased
        assert players[1].position == "BN"  # Uppercased

    @pytest.mark.asyncio
    async def test_parse_yahoo_roster_handles_malformed_data(self):
        """Test graceful handling of malformed roster data."""
        optimizer = LineupOptimizer()
        malformed_payloads = [
            {},  # Empty dict
            {"roster": None},  # None roster
            {"roster": "not a list"},  # String roster
            {"roster": [None, "string", 123]},  # Invalid entries
        ]

        for payload in malformed_payloads:
            players = await optimizer.parse_yahoo_roster(payload)
            assert players == []


class TestBenchSlots:
    """Test bench slot definitions."""

    def test_bench_slots_contain_expected_values(self):
        """Test that BENCH_SLOTS contains expected values."""
        assert "BN" in BENCH_SLOTS
        assert "BENCH" in BENCH_SLOTS
        assert "IR" in BENCH_SLOTS
        assert "IR+" in BENCH_SLOTS
        assert "NA" in BENCH_SLOTS

    def test_bench_slots_excludes_active_positions(self):
        """Test that active positions are not in BENCH_SLOTS."""
        assert "QB" not in BENCH_SLOTS
        assert "RB" not in BENCH_SLOTS
        assert "WR" not in BENCH_SLOTS
        assert "TE" not in BENCH_SLOTS
        assert "FLEX" not in BENCH_SLOTS


class TestMatchConfidenceScores:
    """Test match confidence score definitions."""

    def test_match_confidence_exact_highest(self):
        """Test that exact matches have highest confidence."""
        assert MATCH_CONFIDENCE["exact"] == 1.0
        assert MATCH_CONFIDENCE["exact"] >= MATCH_CONFIDENCE["normalized"]
        assert MATCH_CONFIDENCE["exact"] >= MATCH_CONFIDENCE["fuzzy"]

    def test_match_confidence_decreasing_order(self):
        """Test that confidence decreases with match quality."""
        assert MATCH_CONFIDENCE["exact"] > MATCH_CONFIDENCE["normalized"]
        assert MATCH_CONFIDENCE["normalized"] > MATCH_CONFIDENCE["variant"]
        assert MATCH_CONFIDENCE["variant"] > MATCH_CONFIDENCE["token_subset"]
        assert MATCH_CONFIDENCE["token_subset"] > MATCH_CONFIDENCE["fuzzy"]
        assert MATCH_CONFIDENCE["fuzzy"] > MATCH_CONFIDENCE["failed"]

    def test_match_confidence_failed_is_zero(self):
        """Test that failed matches have zero confidence."""
        assert MATCH_CONFIDENCE["failed"] == 0.0


class TestDynamicWeights:
    """Projection blend weights (issue #27: they used to sum to 0.8)."""

    @pytest.mark.parametrize("confidence", [0.0, 0.25, 0.5, 0.85, 1.0])
    def test_weights_sum_to_one_when_both_sources_present(self, confidence):
        w = _calculate_dynamic_weights(16.0, 14.0, confidence)
        assert w["yahoo"] + w["sleeper"] == pytest.approx(1.0)

    def test_identical_projections_blend_to_themselves(self):
        w = _calculate_dynamic_weights(16.0, 16.0, 1.0)
        assert 16.0 * w["yahoo"] + 16.0 * w["sleeper"] == pytest.approx(16.0)

    def test_full_confidence_is_an_even_split(self):
        w = _calculate_dynamic_weights(10.0, 20.0, 1.0)
        assert w["yahoo"] == pytest.approx(0.5)
        assert w["sleeper"] == pytest.approx(0.5)

    def test_lower_confidence_shifts_weight_to_yahoo(self):
        sure = _calculate_dynamic_weights(10.0, 20.0, 1.0)
        shaky = _calculate_dynamic_weights(10.0, 20.0, 0.4)
        assert shaky["sleeper"] < sure["sleeper"]
        assert shaky["yahoo"] > sure["yahoo"]

    def test_missing_yahoo_projection_uses_sleeper_in_full(self):
        w = _calculate_dynamic_weights(0.0, 18.0, 0.85)
        assert (w["yahoo"], w["sleeper"]) == (0.0, 1.0)

    def test_missing_sleeper_projection_uses_yahoo_in_full(self):
        w = _calculate_dynamic_weights(12.0, 0.0, 1.0)
        assert (w["yahoo"], w["sleeper"]) == (1.0, 0.0)

    def test_unmatched_sleeper_player_is_ignored(self):
        w = _calculate_dynamic_weights(12.0, 18.0, 0.0)
        assert (w["yahoo"], w["sleeper"]) == (1.0, 0.0)

    def test_no_unused_other_key(self):
        assert "other" not in _calculate_dynamic_weights(10.0, 10.0, 1.0)
