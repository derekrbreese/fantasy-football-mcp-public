"""Tests for evidence-gated breakout labels."""

from copy import deepcopy
from datetime import datetime, timezone

from src.agents.breakout_watch import evaluate_breakout_watch

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _candidate(
    index: int,
    *,
    points: float,
    opportunities: float,
    experience: int = 2,
    position: str = "RB",
) -> dict:
    return {
        "name": f"Player {index}",
        "position": position,
        "team": "TST",
        "rank": index,
        "average_draft_position": 200 - index,
        "breakout_evidence": {
            "source": "Example Projections",
            "as_of": "2026-08-20",
            "projected_points": points,
            "projected_opportunities": opportunities,
            "opportunity_kind": "touches" if position == "RB" else "targets",
            "experience_years": experience,
        },
    }


def test_labels_only_young_players_with_fresh_projection_and_opportunity_evidence() -> None:
    rankings = [
        _candidate(1, points=110, opportunities=120),
        _candidate(2, points=120, opportunities=135),
        _candidate(3, points=135, opportunities=150),
        _candidate(4, points=155, opportunities=180),
        _candidate(5, points=180, opportunities=220),
    ]

    result = evaluate_breakout_watch(rankings, now=NOW)

    assert result["status"] == "available"
    assert result["coveragePositions"] == ["RB"]
    label = result["labels"][4]
    assert label["label"] == "Breakout Watch"
    assert label["source"] == "Example Projections"
    assert label["asOf"] == "2026-08-20"
    assert label["projectedPoints"] == 180
    assert label["projectedOpportunities"] == 220
    assert label["opportunityKind"] == "touches"
    assert label["experienceYears"] == 2
    assert label["calibrated"] is False
    assert result["labels"][0] is None


def test_adp_or_headline_sentiment_never_creates_breakout_evidence() -> None:
    rankings = [
        {
            "name": "Hyped Player",
            "position": "WR",
            "rank": 10,
            "average_draft_position": 150,
            "recentNews": [{"headline": "Expected to break out and dominate"}],
        }
    ]

    result = evaluate_breakout_watch(rankings, now=NOW)

    assert result["status"] == "unavailable"
    assert result["labels"] == [None]
    assert "projection" in result["message"].lower()
    assert "opportunity" in result["message"].lower()


def test_stale_wrong_kind_and_veteran_evidence_do_not_receive_labels() -> None:
    rankings = [
        _candidate(index, points=100 + index * 10, opportunities=100 + index * 15)
        for index in range(1, 7)
    ]
    rankings[3]["breakout_evidence"]["as_of"] = "2026-05-01"
    rankings[4]["breakout_evidence"]["opportunity_kind"] = "targets"
    rankings[5]["breakout_evidence"]["experience_years"] = 8

    result = evaluate_breakout_watch(rankings, now=NOW)

    assert result["labels"][3:] == [None, None, None]


def test_headlines_cannot_change_a_projection_derived_result() -> None:
    rankings = [
        _candidate(index, points=100 + index * 12, opportunities=100 + index * 18)
        for index in range(1, 6)
    ]
    with_news = deepcopy(rankings)
    with_news[-1]["recentNews"] = [
        {"headline": "Bad headline"},
        {"headline": "Great opportunity"},
    ]

    assert evaluate_breakout_watch(rankings, now=NOW) == evaluate_breakout_watch(
        with_news, now=NOW
    )


def test_receptions_and_targets_form_distinct_projection_cohorts() -> None:
    rankings = [
        _candidate(
            index,
            points=100 + index * 12,
            opportunities=50 + index * 4,
            position="WR",
        )
        for index in range(1, 7)
    ]
    for candidate in rankings[:3]:
        candidate["breakout_evidence"]["opportunity_kind"] = "receptions"

    split = evaluate_breakout_watch(rankings, now=NOW)

    assert split["status"] == "unavailable"
    assert split["labels"] == [None] * 6

    for candidate in rankings[3:5]:
        candidate["breakout_evidence"]["opportunity_kind"] = "receptions"
    receptions = evaluate_breakout_watch(rankings, now=NOW)

    assert receptions["status"] == "available"
    assert receptions["coveragePositions"] == ["WR"]
    assert any(
        label and label["opportunityKind"] == "receptions"
        for label in receptions["labels"]
    )


def test_rejects_unsafe_or_noncanonical_evidence_inside_the_pure_boundary() -> None:
    rankings = [
        _candidate(index, points=100 + index * 12, opportunities=100 + index * 18)
        for index in range(1, 6)
    ]
    rankings[0]["breakout_evidence"]["source"] = "https://example.test/?token=secret"
    rankings[1]["breakout_evidence"]["private_notes"] = "manager secret"
    rankings[2]["breakout_evidence"]["source"] = "/Users/private/projections.csv"

    result = evaluate_breakout_watch(rankings, now=NOW)

    assert result["status"] == "unavailable"
    assert result["evidencePlayers"] == 2
    assert result["labels"] == [None] * 5


def test_does_not_consume_rankings_beyond_the_evidence_budget() -> None:
    consumed = 0

    def candidates():
        nonlocal consumed
        for index in range(700):
            consumed += 1
            yield _candidate(index, points=100, opportunities=100)

    result = evaluate_breakout_watch(candidates(), now=NOW)

    assert consumed == 500
    assert len(result["labels"]) == 500
