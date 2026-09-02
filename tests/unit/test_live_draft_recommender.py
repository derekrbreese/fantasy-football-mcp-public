"""Behavioral tests for the live-draft recommendation specialist suite."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.agents.live_draft_recommender import (
    LiveDraftRecommendationEngine,
    _same_player,
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


def test_yahoo_player_keys_take_precedence_over_ambiguous_names_and_suffixes() -> None:
    keyed_pick = {
        "player": "B. Robinson Jr.",
        "position": "RB",
        "nflTeam": "WAS",
        "playerKey": "461.p.33536",
    }

    assert _same_player(
        keyed_pick,
        {
            "name": "Brian Robinson Jr.",
            "position": "RB",
            "team": "WAS",
            "player_key": "461.p.33536",
        },
    )
    assert not _same_player(
        keyed_pick,
        {
            "name": "Brian Robinson Sr.",
            "position": "RB",
            "team": "WAS",
            "player_key": "461.p.99999",
        },
    )
    assert _same_player(
        keyed_pick,
        {"name": "Brian Robinson Jr.", "position": "RB", "team": "WAS"},
    )


def test_yahoo_player_keys_disambiguate_dst_before_team_fallback() -> None:
    pick = {
        "player": "San Francisco 49ers",
        "position": "DEF",
        "nflTeam": "SF",
        "playerKey": "461.p.100042",
    }
    assert _same_player(
        pick,
        {
            "name": "49ers D/ST",
            "position": "DST",
            "team": "SF",
            "player_key": "461.p.100042",
        },
    )
    assert not _same_player(
        pick,
        {
            "name": "49ers D/ST",
            "position": "DST",
            "team": "SF",
            "player_key": "461.p.100099",
        },
    )


def test_recommendations_propagate_only_a_valid_yahoo_player_key() -> None:
    context = live_context()
    candidates = rankings()
    candidates[-1]["player_key"] = "461.p.100042"

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        context, candidates, {"teams": 4}, count=10
    )
    keyed = next(
        item
        for item in result["recommendations"]
        if item["player"]["name"] == candidates[-1]["name"]
    )

    assert keyed["player"]["playerKey"] == "461.p.100042"
def test_candidate_output_distinguishes_missing_adp_from_rank_fallback() -> None:
    candidate_pool = [
        *rankings(),
        {
            "name": "No Market ADP",
            "position": "WR",
            "team": "SEA",
            "rank": 13,
        },
    ]

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), candidate_pool, {"teams": 4}, count=20
    )

    candidate = next(
        item for item in result["recommendations"]
        if item["player"]["name"] == "No Market ADP"
    )
    assert candidate["player"]["adp"] is None
    assert candidate["player"]["adpAvailable"] is False
    assert candidate["specialistDetails"]["value"]["adp"] is None
    assert candidate["specialistDetails"]["value"]["adpAvailable"] is False
    assert candidate["specialistDetails"]["value"]["adpDelta"] is None
    assert "ADP unavailable" in candidate["reasoning"][0]
    brief = LiveDraftRecommendationEngine._candidate_brief(candidate)
    assert brief["adp"] is None
    assert brief["adpAvailable"] is False


def test_primary_candidate_keeps_combined_draft_intelligence_contract() -> None:
    target_evidence = {
        "source": "Example Projections",
        "as_of": "2026-08-20",
        "projected_points": 300,
        "projected_opportunities": 160,
        "opportunity_kind": "targets",
        "experience_years": 2,
    }
    supporting_candidates = [
        {
            "name": f"Evidence Receiver {index}",
            "position": "WR",
            "team": "BUF",
            "rank": 40 + index,
            "average_draft_position": 80 + index,
            "breakout_evidence": {
                "source": "Example Projections",
                "as_of": "2026-08-20",
                "projected_points": 100 + index * 20,
                "projected_opportunities": 70 + index * 10,
                "opportunity_kind": "targets",
                "experience_years": 4,
            },
        }
        for index in range(1, 5)
    ]
    target = {
        "name": "Integrated Prospect",
        "position": "WR",
        "team": "SEA",
        "rank": 7,
        "player_key": "461.p.33536",
        "breakout_evidence": target_evidence,
    }

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        [*rankings()[:6], target, *supporting_candidates],
        {"teams": 4},
        count=10,
        now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        market_source={
            "name": "unit-test rankings",
            "season": 2026,
            "asOf": "2026-09-01",
        },
    )

    primary = result["primaryRecommendation"]
    assert primary == result["recommendations"][0]
    assert primary["player"] == {
        "name": "Integrated Prospect",
        "position": "WR",
        "team": "SEA",
        "playerKey": "461.p.33536",
        "rank": 7.0,
        "adp": None,
        "adpAvailable": False,
        "byeWeek": None,
    }
    assert primary["specialistDetails"]["value"]["adp"] is None
    assert primary["specialistDetails"]["value"]["adpAvailable"] is False
    assert primary["specialistDetails"]["value"]["adpDelta"] is None
    assert primary["breakoutWatch"] == {
        "label": "Breakout Watch",
        "method": (
            "fresh same-source position cohort: projected points and projected "
            "opportunities at or above the 60th percentile, with three or fewer "
            "years of experience"
        ),
        "source": "Example Projections",
        "asOf": "2026-08-20",
        "projectedPoints": 300.0,
        "projectedOpportunities": 160.0,
        "opportunityKind": "targets",
        "experienceYears": 2,
        "pointsPercentile": 1.0,
        "opportunityPercentile": 1.0,
        "calibrated": False,
    }
    assert set(primary["decisionSignals"]) == {"badges", "action", "riskCaution"}
    assert primary["decisionSignals"]["badges"] == []
    assert primary["decisionSignals"]["action"]["code"] == "timing-unknown"
    assert primary["decisionSignals"]["action"]["calibrated"] is False
    assert result["marketSignals"]["status"] == "available"
    assert result["nextTwoPicksPlan"]["primaryNow"] == {
        "name": "Integrated Prospect",
        "position": "WR",
        "team": "SEA",
        "score": primary["overallScore"],
    }


def test_recommender_normalizes_jaguars_team_alias_for_initialed_picks() -> None:
    context = live_context()
    context["picks"].extend(
        [
            {
                "pickNumber": 7,
                "player": "B. Tuten",
                "position": "RB",
                "nflTeam": "JAX",
                "fantasyTeam": "Alpha",
                "isUserPick": False,
            },
            {
                "pickNumber": 8,
                "player": "P. Washington",
                "position": "WR",
                "nflTeam": "JAX",
                "fantasyTeam": "Beta",
                "isUserPick": False,
            },
        ]
    )
    candidate_pool = [
        *rankings(),
        {
            "name": "Bhayshul Tuten",
            "position": "RB",
            "team": "JAC",
            "average_draft_position": 59.0,
            "rank": 59,
        },
        {
            "name": "Parker Washington",
            "position": "WR",
            "team": "JAC",
            "average_draft_position": 64.0,
            "rank": 64,
        },
        {
            "name": "Baxter Tuten",
            "position": "RB",
            "team": "TEN",
            "average_draft_position": 200.0,
            "rank": 200,
        },
    ]

    result = LiveDraftRecommendationEngine(simulations=8).recommend(
        context, candidate_pool, {"teams": 4}, count=20
    )

    names = [item["player"]["name"] for item in result["recommendations"]]
    assert "Bhayshul Tuten" not in names
    assert "Parker Washington" not in names
    assert "Baxter Tuten" in names
    assert not any("could not be resolved" in warning for warning in result["warnings"])
    assert result["critic"]["checks"]["allDraftedPlayersResolved"] is True


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
    assert first["nextTwoPicksPlan"]["primaryNow"]["name"] == first["primaryRecommendation"]["player"]["name"]
    assert first["nextTwoPicksPlan"]["nextUserPicks"] == [11, 14]
    assert first["nextTwoPicksPlan"]["probabilitiesCalibrated"] is False

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


def test_cockpit_returns_bounded_strategy_tiers_runs_fallbacks_and_readiness() -> None:
    result = LiveDraftRecommendationEngine(simulations=32, random_seed=17).recommend(
        live_context(),
        rankings(),
        {
            "teams": 4,
            "roster_positions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "FLEX", "count": 1},
                {"position": "BN", "count": 6},
            ],
        },
        count=3,
    )

    cockpit = result["cockpit"]
    assert set(cockpit) == {
        "strategyComparison",
        "positionBoards",
        "positionRuns",
        "rosterPlan",
        "fallbackTiers",
        "readiness",
        "recap",
        "breakoutWatch",
    }
    assert [entry["strategy"] for entry in cockpit["strategyComparison"]["strategies"]] == [
        "conservative",
        "balanced",
        "aggressive",
    ]
    assert all(entry["primary"]["name"] for entry in cockpit["strategyComparison"]["strategies"])
    assert len(cockpit["positionBoards"]) <= 8
    assert all(len(board["candidates"]) <= 5 for board in cockpit["positionBoards"])
    assert {run["position"] for run in cockpit["positionRuns"]} == {"RB", "WR"}
    assert cockpit["fallbackTiers"]
    assert all(len(tier["candidates"]) <= 3 for tier in cockpit["fallbackTiers"])
    assert cockpit["readiness"]["ready"] is True
    assert all(isinstance(check["passed"], bool) for check in cockpit["readiness"]["checks"])


def test_breakout_watch_requires_fresh_explicit_projection_and_opportunity_evidence() -> None:
    candidates = rankings()
    candidates.extend(
        {
            "name": f"Evidence Runner {index}",
            "position": "RB",
            "team": "BUF",
            "rank": 20 + index,
            "average_draft_position": 100 + index,
            "recentNews": [{"headline": "This text must not affect the label"}],
            "breakout_evidence": {
                "source": "Example Projections",
                "as_of": "2026-08-20",
                "projected_points": 100 + index * 20,
                "projected_opportunities": 100 + index * 25,
                "opportunity_kind": "touches",
                "experience_years": 2,
            },
        }
        for index in range(1, 6)
    )

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        candidates,
        {"teams": 4},
        count=20,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    labeled = [item for item in result["recommendations"] if "breakoutWatch" in item]
    assert labeled
    assert all(item["breakoutWatch"]["label"] == "Breakout Watch" for item in labeled)
    assert all(item["breakoutWatch"]["calibrated"] is False for item in labeled)
    assert result["cockpit"]["breakoutWatch"] == {
        "status": "available",
        "method": (
            "explicit fresh sourced projection and opportunity evidence; same-position, "
            "same-source cohort heuristic; ADP and news are excluded"
        ),
        "calibrated": False,
        "coveragePositions": ["RB"],
        "evidencePlayers": 5,
        "message": (
            "Breakout labels use fresh sourced projection and opportunity evidence and are "
            "uncalibrated."
        ),
    }


def test_missing_breakout_evidence_is_explained_without_degrading_recommendations() -> None:
    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), rankings(), {"teams": 4}, count=5
    )

    assert result["status"] == "success"
    assert all("breakoutWatch" not in item for item in result["recommendations"])
    assert result["capabilities"]["breakoutWatch"] is False
    assert result["cockpit"]["breakoutWatch"]["status"] == "unavailable"
    assert "projection" in result["cockpit"]["breakoutWatch"]["message"].lower()


def test_fantasypros_projection_evidence_is_output_only_and_strictly_allowlisted() -> None:
    source = rankings()
    target = next(item for item in source if item["name"] == "CeeDee Lamb")
    target.update(
        {
            "projected_points": 294.5,
            "projected_opportunities": 124.25,
            "projection_opportunity_kind": "receptions",
            "projection_source": "FantasyPros",
            "projection_season": 2026,
            "projection_scoring": "PPR",
            "projection_source_as_of": None,
            "projection_fetched_at": "2026-08-28T16:00:00Z",
            "projection_stale": False,
            "projection_url": "https://evil.test/?token=secret",
            "projection_raw_payload": {"manager": "private"},
        }
    )
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    baseline = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), rankings(), {"teams": 4}, count=5, now=now
    )
    enriched = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), source, {"teams": 4}, count=5, now=now
    )

    assert [item["player"]["name"] for item in enriched["recommendations"]] == [
        item["player"]["name"] for item in baseline["recommendations"]
    ]
    assert [item["overallScore"] for item in enriched["recommendations"]] == [
        item["overallScore"] for item in baseline["recommendations"]
    ]
    candidate = next(
        item for item in enriched["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert candidate["projectionEvidence"] == {
        "source": "FantasyPros",
        "season": 2026,
        "scoring": "PPR",
        "sourceAsOf": None,
        "fetchedAt": "2026-08-28T16:00:00Z",
        "stale": False,
        "projectedPoints": 294.5,
        "projectedOpportunities": 124.25,
        "opportunityKind": "receptions",
    }
    assert "breakoutWatch" not in candidate
    assert "evil.test" not in repr(candidate)
    assert "private" not in repr(candidate)


@pytest.mark.parametrize(
    ("override",),
    [
        ({"projection_source": "https://evil.test/?token=secret"},),
        ({"projection_season": True},),
        ({"projection_scoring": "CUSTOM"},),
        ({"projection_fetched_at": "not-a-timestamp"},),
        ({"projection_stale": "false"},),
        ({"projected_opportunities": float("inf")},),
        ({"projection_opportunity_kind": "touches"},),
    ],
)
def test_malformed_fantasypros_projection_evidence_is_omitted(
    override: dict,
) -> None:
    source = rankings()
    target = next(item for item in source if item["name"] == "CeeDee Lamb")
    target.update(
        {
            "projected_points": 294.5,
            "projected_opportunities": 124.25,
            "projection_opportunity_kind": "receptions",
            "projection_source": "FantasyPros",
            "projection_season": 2026,
            "projection_scoring": "PPR",
            "projection_source_as_of": None,
            "projection_fetched_at": "2026-08-28T16:00:00Z",
            "projection_stale": False,
            **override,
        }
    )

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        source,
        {"teams": 4},
        count=5,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    candidate = next(
        item for item in result["recommendations"] if item["player"]["name"] == "CeeDee Lamb"
    )
    assert "projectionEvidence" not in candidate


def test_breakout_classification_stays_stable_as_the_ledger_advances() -> None:
    candidates = rankings()
    candidates.extend(
        {
            "name": f"Evidence Runner {index}",
            "position": "RB",
            "team": "BUF",
            "rank": 20 + index,
            "average_draft_position": 100 + index,
            "breakout_evidence": {
                "source": "Example Projections",
                "as_of": "2026-08-20",
                "projected_points": 100 + index * 20,
                "projected_opportunities": 100 + index * 25,
                "opportunity_kind": "touches",
                "experience_years": 2,
            },
        }
        for index in range(1, 6)
    )
    advanced = live_context()
    advanced["picks"].append(
        {
            "pickNumber": 7,
            "player": "Evidence Runner 1",
            "position": "RB",
            "nflTeam": "BUF",
            "fantasyTeam": "Alpha",
            "isUserPick": False,
        }
    )

    before = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(), candidates, {"teams": 4}, count=20, now=datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    after = LiveDraftRecommendationEngine(simulations=0).recommend(
        advanced, candidates, {"teams": 4}, count=20, now=datetime(2026, 9, 1, tzinfo=timezone.utc)
    )

    before_label = next(
        item["breakoutWatch"]
        for item in before["recommendations"]
        if item["player"]["name"] == "Evidence Runner 5"
    )
    after_label = next(
        item["breakoutWatch"]
        for item in after["recommendations"]
        if item["player"]["name"] == "Evidence Runner 5"
    )
    assert before_label == after_label
    assert after["cockpit"]["breakoutWatch"]["evidencePlayers"] == 5


def test_cockpit_roster_plan_and_recap_use_configured_slots_and_adp_only() -> None:
    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        rankings(),
        {
            "teams": 4,
            "roster_positions": [
                {"position": "RB", "count": 1},
                {"position": "WR", "count": 1},
            ],
        },
        count=3,
    )

    roster = result["cockpit"]["rosterPlan"]
    assert roster["starterComplete"] is True
    assert roster["openStarterSlots"] == 0
    assert roster["slots"] == [
        {"position": "RB", "current": 1, "required": 1, "open": 0},
        {"position": "WR", "current": 1, "required": 1, "open": 0},
    ]

    recap = result["cockpit"]["recap"]
    assert recap["complete"] is False
    assert recap["expectedPicks"] == 8
    assert recap["recordedPicks"] == 6
    assert recap["userPickCount"] == 2
    assert len(recap["decisions"]) == 2
    assert all(decision["basis"] == "uncalibrated ADP heuristic" for decision in recap["decisions"])

    invalid_adp_rankings = rankings()
    invalid_adp_rankings[2]["average_draft_position"] = True
    invalid_adp_rankings[5]["average_draft_position"] = float("inf")
    invalid_recap = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        invalid_adp_rankings,
        {"teams": 4},
    )["cockpit"]["recap"]
    assert invalid_recap["decisions"] == []


def test_blocked_cockpit_explains_readiness_without_candidate_availability() -> None:
    context = live_context()
    context["picks"] = [pick for pick in context["picks"] if pick["pickNumber"] != 4]

    result = LiveDraftRecommendationEngine().recommend(context, rankings(), {"teams": 4})

    cockpit = result["cockpit"]
    assert cockpit["readiness"]["ready"] is False
    assert cockpit["strategyComparison"]["strategies"] == []
    assert cockpit["positionBoards"] == []
    assert cockpit["positionRuns"] == []
    assert cockpit["fallbackTiers"] == []
    assert cockpit["recap"]["status"] == "blocked"


def test_gap_in_numbered_ledger_blocks_player_recommendations() -> None:
    context = live_context()
    context["picks"] = [pick for pick in context["picks"] if pick["pickNumber"] != 4]

    result = LiveDraftRecommendationEngine().recommend(context, rankings(), {"teams": 4})

    assert result["status"] == "blocked"
    assert result["recommendations"] == []
    assert result["state"]["health"]["missingPickNumbers"] == [4]
    assert any("gap" in warning.lower() for warning in result["warnings"])
    assert result["nextTwoPicksPlan"]["status"] == "blocked"
    assert result["nextTwoPicksPlan"]["primaryNow"] is None


def test_authoritative_capture_integrity_blocks_complete_ledger_recommendations() -> None:
    context = live_context()
    context["captureBlocked"] = True

    state = reconcile_live_draft(context, team_count=4)
    result = LiveDraftRecommendationEngine().recommend(
        context, rankings(), {"teams": 4}
    )

    assert state["health"]["complete"] is False
    assert state["health"]["authoritativeCaptureBlocked"] is True
    assert result["status"] == "blocked"
    assert result["recommendations"] == []
    assert any(
        "capture integrity" in warning.lower() for warning in result["warnings"]
    )


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
    for recommendation in result["recommendations"]:
        assert recommendation["effectiveWeights"]["riskNews"] == 0
        assert sum(recommendation["effectiveWeights"].values()) == pytest.approx(1)


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


def test_market_sleeper_watch_is_bounded_transparent_and_deterministic() -> None:
    observed_at = "2026-09-02T12:00:00Z"
    candidate_pool = rankings()
    for index, (name, adp) in enumerate(
        [
            ("Alpha Sleeper", 45.0),
            ("Beta Sleeper", 44.0),
            ("Gamma Sleeper", 43.0),
            ("Delta Sleeper", 42.0),
            ("Epsilon Sleeper", 41.0),
            ("Zeta Sleeper", 40.0),
        ],
        start=13,
    ):
        candidate = {
            "name": name,
            "position": "WR",
            "team": "SEA",
            "rank": index,
            "average_draft_position": adp,
        }
        if name == "Delta Sleeper":
            candidate.update(
                {
                    "injury_status": "questionable",
                    "injury_source": "FantasyPros",
                    "injury_updated_at": observed_at,
                    "injury_snapshot_at": observed_at,
                    "injury_fresh": True,
                }
            )
        candidate_pool.append(candidate)
    candidate_pool.append(
        {
            "name": "Slipped Value",
            "position": "TE",
            "team": "DEN",
            "rank": 2,
            "average_draft_position": 2.5,
        }
    )
    source = {
        "name": "unit-test rankings",
        "season": 2026,
        "asOf": "2026-09-01",
    }
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    engine = LiveDraftRecommendationEngine(simulations=8, random_seed=19)

    first = engine.recommend(
        live_context(),
        candidate_pool,
        {"teams": 4},
        count=20,
        now=now,
        market_source=source,
    )
    second = engine.recommend(
        live_context(),
        candidate_pool,
        {"teams": 4},
        count=20,
        now=now,
        market_source=source,
    )

    market = first["marketSignals"]
    assert market == second["marketSignals"]
    assert market["status"] == "available"
    assert market["calibrated"] is False
    assert "uncalibrated" in market["method"].lower()
    assert market["source"] == {
        "name": "unit-test rankings",
        "season": 2026,
        "targetSeason": 2026,
        "sameSeason": True,
        "asOf": "2026-09-01",
        "asOfBasis": "source",
    }
    assert [item["player"]["name"] for item in market["sleeperWatch"]] == [
        "Alpha Sleeper",
        "Beta Sleeper",
        "Gamma Sleeper",
        "Delta Sleeper",
        "Epsilon Sleeper",
    ]
    assert market["sleeperWatch"][0]["discountPicks"] == 32.0
    assert market["sleeperWatch"][0]["discountRounds"] == 8.0
    assert market["sleeperWatch"][0]["marketRound"] == 12
    assert market["sleeperWatch"][0]["action"]["code"] == "can-wait"
    delta = next(
        item
        for item in market["sleeperWatch"]
        if item["player"]["name"] == "Delta Sleeper"
    )
    assert "questionable" in delta["riskCaution"]["message"]
    exclusions = {item["code"]: item for item in market["exclusions"]}
    assert exclusions["drafted"]["count"] == 6
    assert exclusions["unresolved-drafted-identity"]["count"] == 0
    assert exclusions["no-real-adp"]["count"] == 0
    assert exclusions["outside-displayed-frontier"]["count"] == 1
    slipped = next(
        item
        for item in first["recommendations"]
        if item["player"]["name"] == "Slipped Value"
    )
    assert slipped["decisionSignals"]["action"]["code"] == "take-now"
    assert {badge["code"] for badge in slipped["decisionSignals"]["badges"]} == {
        "value"
    }


@pytest.mark.parametrize(
    "invalid_adp",
    [None, True, False, float("nan"), float("inf"), float("-inf")],
)
def test_market_signals_never_treat_rank_fallback_as_real_adp(
    invalid_adp: object,
) -> None:
    candidate_pool = [
        *rankings()[:6],
        {
            "name": "No Market ADP",
            "position": "WR",
            "team": "SEA",
            "rank": 40,
            "average_draft_position": invalid_adp,
        },
    ]

    result = LiveDraftRecommendationEngine(simulations=0).recommend(
        live_context(),
        candidate_pool,
        {"teams": 4},
        count=10,
        now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        market_source={"name": "test", "season": 2026, "asOf": "2026-09-01"},
    )

    player = result["recommendations"][0]
    assert player["player"]["name"] == "No Market ADP"
    assert player["player"]["adp"] is None
    assert player["specialistDetails"]["value"]["adpAvailable"] is False
    assert player["specialistDetails"]["value"]["adpDelta"] is None
    assert player["decisionSignals"]["badges"] == []
    assert player["decisionSignals"]["action"]["code"] == "timing-unknown"
    assert result["marketSignals"]["status"] == "unavailable"
    exclusions = {
        item["code"]: item for item in result["marketSignals"]["exclusions"]
    }
    assert exclusions["no-real-adp"]["count"] == 1


def test_sleeper_watch_fails_closed_for_ledger_identity_and_source_season() -> None:
    sleeper = {
        "name": "Late Market Target",
        "position": "WR",
        "team": "SEA",
        "rank": 13,
        "average_draft_position": 40.0,
    }
    source = {"name": "test", "season": 2026, "asOf": "2026-09-01"}
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    engine = LiveDraftRecommendationEngine(simulations=0)

    incomplete = live_context()
    incomplete["picks"] = [pick for pick in incomplete["picks"] if pick["pickNumber"] != 3]
    blocked = engine.recommend(
        incomplete,
        [*rankings(), sleeper],
        {"teams": 4},
        now=now,
        market_source=source,
    )
    assert blocked["status"] == "blocked"
    assert blocked["marketSignals"]["status"] == "blocked"
    assert blocked["marketSignals"]["sleeperWatch"] == []
    assert next(
        item
        for item in blocked["marketSignals"]["trust"]
        if item["code"] == "ledger-complete"
    )["passed"] is False
    assert next(
        item
        for item in blocked["marketSignals"]["trust"]
        if item["code"] == "drafted-identities-resolved"
    )["passed"] is False

    unresolved_pool = [*rankings()[1:], sleeper]
    unresolved = engine.recommend(
        live_context(),
        unresolved_pool,
        {"teams": 4},
        now=now,
        market_source=source,
    )
    assert unresolved["status"] == "degraded"
    assert unresolved["marketSignals"]["status"] == "blocked"
    assert unresolved["marketSignals"]["sleeperWatch"] == []
    exclusions = {
        item["code"]: item for item in unresolved["marketSignals"]["exclusions"]
    }
    assert exclusions["unresolved-drafted-identity"]["count"] == 1

    wrong_season = engine.recommend(
        live_context(),
        [*rankings(), sleeper],
        {"teams": 4},
        now=now,
        market_source={"name": "old rankings", "season": 2025, "asOf": "2025-09-01"},
    )
    assert wrong_season["marketSignals"]["status"] == "unavailable"
    assert wrong_season["marketSignals"]["source"]["sameSeason"] is False
    assert wrong_season["marketSignals"]["sleeperWatch"] == []

    imported_without_source_date = engine.recommend(
        live_context(),
        [*rankings(), sleeper],
        {"teams": 4},
        now=now,
        market_source={
            "name": "undated import",
            "season": 2026,
            "asOf": "2026-09-01T12:00:00Z",
            "asOfBasis": "imported",
        },
    )
    assert imported_without_source_date["marketSignals"]["status"] == "unavailable"
    assert imported_without_source_date["marketSignals"]["source"]["sameSeason"] is True
    assert imported_without_source_date["marketSignals"]["sleeperWatch"] == []

    mismatched_source_date = engine.recommend(
        live_context(),
        [*rankings(), sleeper],
        {"teams": 4},
        now=now,
        market_source={"name": "bad date", "season": 2026, "asOf": "2025-09-01"},
    )
    assert mismatched_source_date["marketSignals"]["status"] == "unavailable"
    assert mismatched_source_date["marketSignals"]["source"]["asOf"] is None
