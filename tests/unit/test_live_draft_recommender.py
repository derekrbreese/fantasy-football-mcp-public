"""Behavioral tests for the live-draft recommendation specialist suite."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.agents.live_draft_recommender import (
    LiveDraftRecommendationEngine,
    reconcile_live_draft,
)


def live_context() -> dict:
    picks = [
        {
            "pickNumber": 1,
            "player": "J. Jefferson",
            "position": "WR",
            "nflTeam": "MIN",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        },
        {
            "pickNumber": 2,
            "player": "C. McCaffrey",
            "position": "RB",
            "nflTeam": "SF",
            "fantasyTeam": "Beta",
            "isUserPick": False,
        },
        {
            "pickNumber": 3,
            "player": "S. Barkley",
            "position": "RB",
            "nflTeam": "PHI",
            "fantasyTeam": "Your Team",
            "isUserPick": True,
        },
        {
            "pickNumber": 4,
            "player": "J. Chase",
            "position": "WR",
            "nflTeam": "CIN",
            "fantasyTeam": "Delta",
            "isUserPick": False,
        },
        {
            "pickNumber": 5,
            "player": "B. Hall",
            "position": "RB",
            "nflTeam": "NYJ",
            "fantasyTeam": "Delta",
            "isUserPick": False,
        },
        {
            "pickNumber": 6,
            "player": "T. Hill",
            "position": "WR",
            "nflTeam": "MIA",
            "fantasyTeam": "Your Team",
            "isUserPick": True,
        },
    ]
    return {
        "schemaVersion": 1,
        "source": "yahoo-draft-recorder",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "draft": {
            "sport": "nfl",
            "leagueId": "10462193",
            "teamId": "6",
            "sessionKey": "nfl:10462193",
        },
        "summary": {
            "totalPicks": 6,
            "latestOverallPick": 6,
            "nextOverallPick": 7,
            "userPickCount": 2,
        },
        "userRoster": [picks[2], picks[5]],
        "teamRosters": {},
        "picks": picks,
    }


def rankings() -> list[dict]:
    return [
        {
            "name": "Justin Jefferson",
            "position": "WR",
            "team": "MIN",
            "average_draft_position": 1.5,
            "rank": 1,
        },
        {
            "name": "Christian McCaffrey",
            "position": "RB",
            "team": "SF",
            "average_draft_position": 2.0,
            "rank": 2,
        },
        {
            "name": "Saquon Barkley",
            "position": "RB",
            "team": "PHI",
            "average_draft_position": 3.0,
            "rank": 3,
        },
        {
            "name": "Ja'Marr Chase",
            "position": "WR",
            "team": "CIN",
            "average_draft_position": 4.0,
            "rank": 4,
        },
        {
            "name": "Breece Hall",
            "position": "RB",
            "team": "NYJ",
            "average_draft_position": 5.0,
            "rank": 5,
        },
        {
            "name": "Tyreek Hill",
            "position": "WR",
            "team": "MIA",
            "average_draft_position": 6.0,
            "rank": 6,
        },
        {
            "name": "CeeDee Lamb",
            "position": "WR",
            "team": "DAL",
            "average_draft_position": 7.0,
            "rank": 7,
            "bye": 10,
        },
        {
            "name": "Josh Allen",
            "position": "QB",
            "team": "BUF",
            "average_draft_position": 8.0,
            "rank": 8,
            "bye": 12,
        },
        {
            "name": "Sam LaPorta",
            "position": "TE",
            "team": "DET",
            "average_draft_position": 12.0,
            "rank": 9,
            "bye": 8,
        },
        {
            "name": "De'Von Achane",
            "position": "RB",
            "team": "MIA",
            "average_draft_position": 14.0,
            "rank": 10,
            "injury_status": "Questionable",
            "injury_source": "FantasyPros",
            "injury_updated_at": datetime.now(timezone.utc).isoformat(),
            "injury_fresh": True,
            "retrievedAt": datetime.now(timezone.utc).isoformat(),
        },
        {
            "name": "Patrick Mahomes",
            "position": "QB",
            "team": "KC",
            "average_draft_position": 18.0,
            "rank": 11,
        },
        {
            "name": "Mark Andrews",
            "position": "TE",
            "team": "BAL",
            "average_draft_position": 22.0,
            "rank": 12,
        },
    ]


def test_reconciler_derives_snake_turn_and_state_health() -> None:
    state = reconcile_live_draft(live_context(), team_count=4)

    assert state["health"]["complete"] is True
    assert state["health"]["missingPickNumbers"] == []
    assert state["currentOverallPick"] == 7
    assert state["userDraftSlot"] == 3
    assert state["nextUserPick"] == 11
    assert state["picksUntilUserTurn"] == 4


def test_recommender_filters_drafted_players_in_initialed_yahoo_ledger() -> None:
    result = LiveDraftRecommendationEngine(simulations=64, random_seed=9).recommend(
        live_context(), rankings(), {"teams": 4}, count=5
    )

    names = [item["player"]["name"] for item in result["recommendations"]]
    assert "Saquon Barkley" not in names
    assert "Tyreek Hill" not in names
    assert "Breece Hall" not in names
    assert set(names).issubset({item["name"] for item in rankings()[6:]})


def test_full_suite_returns_specialists_scenario_critic_and_contingency() -> None:
    engine = LiveDraftRecommendationEngine(simulations=96, random_seed=17)

    first = engine.recommend(live_context(), rankings(), {"teams": 4}, count=3)
    second = engine.recommend(live_context(), rankings(), {"teams": 4}, count=3)

    assert first["status"] == "success"
    assert first["primaryRecommendation"] == first["recommendations"][0]
    assert len(first["alternatives"]) == 2
    assert first["recommendations"] == second["recommendations"]
    assert first["contingency"]["ifPrimaryUnavailable"]
    assert first["state"]["nextUserPick"] == 11
    assert first["capabilities"]["externalNews"] is False
    assert first["capabilities"]["injuryStatus"] is True

    primary = first["primaryRecommendation"]
    assert set(primary["scores"]) == {
        "value",
        "rosterConstruction",
        "draftDynamics",
        "opponentModel",
        "riskNews",
        "scenario",
    }
    assert 0 <= primary["confidence"] <= 1
    assert primary["confidenceCalibrated"] is False
    assert 0 <= primary["returnProbability"] <= 1
    assert primary["rosterImpact"]
    assert primary["reasoning"]
    assert "checks" in first["critic"]
    assert first["critic"]["passed"] is True


def test_gap_in_numbered_ledger_blocks_player_recommendations() -> None:
    context = live_context()
    context["picks"] = [pick for pick in context["picks"] if pick["pickNumber"] != 4]

    result = LiveDraftRecommendationEngine().recommend(context, rankings(), {"teams": 4})

    assert result["status"] == "blocked"
    assert result["recommendations"] == []
    assert result["state"]["health"]["missingPickNumbers"] == [4]
    assert any("gap" in warning.lower() for warning in result["warnings"])


def test_no_rankings_returns_state_advice_without_inventing_players() -> None:
    result = LiveDraftRecommendationEngine().recommend(live_context(), [], {"teams": 4})

    assert result["status"] == "degraded"
    assert result["recommendations"] == []
    assert result["draftAdvice"]
    assert any("ranking" in warning.lower() for warning in result["warnings"])


def test_risk_news_treats_missing_status_as_unknown_not_healthy() -> None:
    result = LiveDraftRecommendationEngine(simulations=16).recommend(
        live_context(), rankings(), {"teams": 4}, count=6
    )

    lamb = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    achane = next(
        item for item in result["recommendations"] if item["player"]["name"] == "De'Von Achane"
    )
    assert lamb["risk"]["status"] == "unknown"
    assert achane["risk"]["status"] == "questionable"
    assert lamb["scores"]["riskNews"] is None
    assert lamb["effectiveWeights"]["riskNews"] == 0
    assert achane["scores"]["riskNews"] == 42
    assert achane["effectiveWeights"]["riskNews"] > 0


def test_stale_or_unattributed_injury_status_is_unknown_and_not_scored() -> None:
    candidates = rankings()
    player = next(item for item in candidates if item["name"] == "CeeDee Lamb")
    player.update(
        {
            "injury_status": "Out",
            "injury_source": "FantasyPros",
            "injury_updated_at": "2026-01-01T00:00:00Z",
            "injury_fresh": False,
        }
    )

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        live_context(), candidates, {"teams": 4}, count=6
    )

    lamb = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert lamb["risk"] == {
        "status": "unknown",
        "source": "FantasyPros",
        "updatedAt": "2026-01-01T00:00:00Z",
        "fresh": False,
        "available": False,
        "basis": "unknown",
        "injuryFresh": False,
        "newsFresh": False,
        "recentNews": [],
    }
    assert lamb["scores"]["riskNews"] is None
    assert lamb["effectiveWeights"]["riskNews"] == 0


def test_scorer_independently_rejects_old_snapshot_even_when_fresh_flag_is_true() -> None:
    candidates = rankings()
    player = next(item for item in candidates if item["name"] == "CeeDee Lamb")
    player.update(
        {
            "injury_status": "out",
            "injury_source": "FantasyPros",
            "injury_updated_at": "2026-01-01T00:00:00Z",
            "injury_snapshot_at": "2026-01-01T00:00:00Z",
            "injury_fresh": True,
        }
    )

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        candidates,
        {"teams": 4},
        count=6,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    lamb = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert lamb["risk"]["status"] == "unknown"
    assert lamb["risk"]["fresh"] is False
    assert lamb["scores"]["riskNews"] is None


def test_recent_news_category_is_evaluated_without_scoring_headline_text() -> None:
    candidates = rankings()
    player = next(item for item in candidates if item["name"] == "CeeDee Lamb")
    player.update(
        {
            "news_source": "FantasyPros",
            "news_updated_at": datetime.now(timezone.utc).isoformat(),
            "news_fresh": True,
            "recentNews": [
                {
                    "headline": "Returns to team drills",
                    "category": "Injuries",
                    "publishedAt": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
    )

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        live_context(), candidates, {"teams": 4}, count=6
    )

    lamb = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert result["capabilities"]["externalNews"] is True
    assert lamb["risk"]["status"] == "unknown"
    assert lamb["risk"]["newsFresh"] is True
    assert lamb["risk"]["recentNews"] == [
        {
            "headline": "Returns to team drills",
            "category": "Injuries",
            "publishedAt": lamb["risk"]["recentNews"][0]["publishedAt"],
        }
    ]
    assert lamb["scores"]["riskNews"] == 48
    assert lamb["effectiveWeights"]["riskNews"] > 0
    assert lamb["risk"]["basis"] == "recent-news-category"
    assert any("headline text was not scored" in reason for reason in lamb["reasoning"])


def test_custom_roster_positions_raise_superflex_qb_priority() -> None:
    context = deepcopy(live_context())
    standard = LiveDraftRecommendationEngine(simulations=16).recommend(
        context, rankings(), {"teams": 4}, count=6
    )
    superflex = LiveDraftRecommendationEngine(simulations=16).recommend(
        context,
        rankings(),
        {
            "teams": 4,
            "roster_positions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "Q/W/R/T", "count": 1},
                {"position": "BN", "position_type": "BN", "count": 6},
            ],
        },
        count=6,
    )

    def score(result: dict, name: str) -> float:
        return next(
            item["overallScore"]
            for item in result["recommendations"]
            if item["player"]["name"] == name
        )

    assert score(superflex, "Josh Allen") > score(standard, "Josh Allen")


def test_missing_risk_feed_is_removed_from_applied_weights() -> None:
    without_status = [
        {key: value for key, value in player.items() if key != "injury_status"}
        for player in rankings()
    ]

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        live_context(), without_status, {"teams": 4}, count=3
    )

    assert result["capabilities"]["injuryStatus"] is False
    assert result["appliedWeights"]["riskNews"] == 0
    assert sum(result["appliedWeights"].values()) == pytest.approx(1)


def test_inferred_team_count_degrades_snake_projections() -> None:
    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        live_context(), rankings(), {}, count=3
    )

    assert result["status"] == "degraded"
    assert result["state"]["health"]["teamCountSource"] == "ledger"
    assert any("team count" in warning.lower() for warning in result["warnings"])


def test_unresolved_drafted_identity_fails_critic_without_hiding_recommendations() -> None:
    context = live_context()
    context["picks"].append(
        {
            "pickNumber": 7,
            "player": "M. Williams",
            "position": "WR",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        }
    )

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, rankings(), {"teams": 4}, count=3
    )

    assert result["status"] == "degraded"
    assert result["recommendations"]
    assert result["critic"]["checks"]["allDraftedPlayersResolved"] is False


def test_simulation_budget_is_bounded() -> None:
    result = LiveDraftRecommendationEngine(simulations=100_000).recommend(
        live_context(), rankings(), {"teams": 4}, count=1
    )

    assert result["specialists"]["scenarioSimulator"]["simulations"] == 512


def test_oversized_authoritative_team_count_is_bounded_before_projections() -> None:
    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), rankings(), {"teams": 1_000_000_000}, count=1
    )

    assert result["state"]["teamCount"] == 20
    assert result["state"]["health"]["teamCountSource"] == "league-clamped"
    assert result["state"]["picksUntilUserTurn"] is not None
    assert result["state"]["picksUntilUserTurn"] <= 39
    assert result["status"] == "degraded"
    assert any("clamped" in warning.lower() for warning in result["warnings"])


def test_naive_generated_time_is_treated_as_utc() -> None:
    context = live_context()
    context["generatedAt"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, rankings(), {"teams": 4}, count=1
    )

    assert result["status"] == "success"


def test_unnumbered_pick_blocks_recommendations() -> None:
    context = live_context()
    context["picks"].append(
        {
            "player": "M. Williams",
            "position": "WR",
            "nflTeam": "LAC",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        }
    )

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, rankings(), {"teams": 4}, count=1
    )

    assert result["status"] == "blocked"
    assert result["state"]["health"]["unnumberedPickCount"] == 1


def test_dst_identity_matches_by_position_and_nfl_team() -> None:
    context = live_context()
    context["picks"].append(
        {
            "pickNumber": 9,
            "roundNumber": 3,
            "roundPick": 1,
            "player": "Ravens",
            "position": "DST",
            "nflTeam": "BAL",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        }
    )
    candidate_pool = [
        {"name": "Baltimore", "position": "DST", "team": "BAL", "rank": 1},
        *rankings(),
    ]

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, candidate_pool, {"teams": 4}, count=6
    )

    assert "Baltimore" not in [item["player"]["name"] for item in result["recommendations"]]


def test_extreme_adp_does_not_overflow_probability_models() -> None:
    candidates = rankings()
    candidates[0]["average_pick"] = 10_000_000_000

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        live_context(), candidates, {"teams": 4}, count=1
    )

    assert result["recommendations"]


def test_stale_state_degrades_recommendation() -> None:
    context = live_context()
    context["generatedAt"] = "2000-01-01T00:00:00Z"

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, rankings(), {"teams": 4}, count=3
    )

    assert result["status"] == "degraded"
    assert result["critic"]["checks"]["stateFresh"] is False
