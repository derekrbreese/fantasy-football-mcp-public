"""Behavioral tests for the bounded deterministic two-pick planner."""

from copy import deepcopy

from src.agents.next_two_picks_planner import plan_next_two_picks


def _state() -> dict:
    return {
        "currentOverallPick": 7,
        "userDraftSlot": 3,
        "nextUserPick": 11,
        "teamCount": 4,
        "health": {
            "complete": True,
            "fresh": True,
            "teamCountSource": "league",
        },
    }


def _candidate(
    name: str,
    position: str,
    score: float,
    adp: float | None,
    *,
    current: int = 0,
    required: int = 1,
    target: int = 3,
) -> dict:
    return {
        "player": {
            "name": name,
            "position": position,
            "team": "TST",
            "rank": score,
            "adp": adp,
            "adpAvailable": adp is not None,
        },
        "overallScore": score,
        "specialistDetails": {
            "rosterConstruction": {
                "positionCount": current,
                "required": required,
                "target": target,
            }
        },
    }


def _recommendations() -> list[dict]:
    return [
        _candidate("Primary Receiver", "WR", 92, 9),
        _candidate("Similar Receiver", "WR", 90, 12),
        _candidate("Needed Runner", "RB", 88, 18, required=2),
        _candidate("Tight End", "TE", 84, 20),
    ]


def test_plans_primary_fallbacks_and_position_aware_next_turn_pairs() -> None:
    result = plan_next_two_picks(_recommendations(), _state())

    assert result["status"] == "degraded"
    assert result["primaryNow"]["name"] == "Primary Receiver"
    assert len(result["fallbacksNow"]) == 2
    assert result["nextUserPicks"] == [11, 14]
    assert result["probabilitiesCalibrated"] is False
    assert result["combinations"][0]["now"]["name"] == "Primary Receiver"
    assert result["combinations"][0]["nextTurn"]["name"] == "Needed Runner"
    assert result["combinations"][0]["positions"] == ["WR", "RB"]
    assert 0 <= result["combinations"][0]["nextTurnAvailabilityProbability"] <= 1
    assert result["combinations"][0]["probabilityCalibrated"] is False
    assert any("opponent" in warning.lower() for warning in result["uncertainties"])


def test_is_deterministic_and_bounds_planner_work_and_output() -> None:
    recommendations = [
        _candidate(f"Player {index:02d}", "RB" if index % 2 else "WR", 100 - index, index + 8)
        for index in range(40)
    ]

    first = plan_next_two_picks(recommendations, _state())
    second = plan_next_two_picks(deepcopy(recommendations), deepcopy(_state()))

    assert first == second
    assert len(first["fallbacksNow"]) <= 2
    assert len(first["combinations"]) <= 3
    assert all(len(item["reasons"]) <= 3 for item in first["combinations"])


def test_does_not_consume_candidates_beyond_the_input_budget() -> None:
    consumed = 0

    def candidates():
        nonlocal consumed
        for index in range(1_000):
            consumed += 1
            yield _candidate(f"Player {index}", "WR", 100 - index / 100, index + 8)

    plan_next_two_picks(candidates(), _state())

    assert consumed == 20


def test_degrades_and_omits_next_turn_pairs_when_snake_order_is_unknown() -> None:
    state = _state()
    state["userDraftSlot"] = None
    state["nextUserPick"] = None

    result = plan_next_two_picks(_recommendations(), state)

    assert result["status"] == "degraded"
    assert result["primaryNow"]["name"] == "Primary Receiver"
    assert result["fallbacksNow"]
    assert result["nextUserPicks"] == []
    assert result["combinations"] == []
    assert any("order" in warning.lower() for warning in result["uncertainties"])


def test_blocks_all_player_proposals_for_an_incomplete_ledger() -> None:
    state = _state()
    state["health"]["complete"] = False

    result = plan_next_two_picks(_recommendations(), state)

    assert result["status"] == "blocked"
    assert result["primaryNow"] is None
    assert result["fallbacksNow"] == []
    assert result["combinations"] == []
    assert "ledger" in result["summary"].lower()


def test_degrades_for_stale_inferred_or_unresolved_availability_and_missing_adp() -> None:
    state = _state()
    state["health"].update({"fresh": False, "teamCountSource": "ledger"})
    recommendations = [
        _candidate("Primary Receiver", "WR", 92, 9),
        _candidate("Needed Runner", "RB", 88, None, required=2),
    ]

    result = plan_next_two_picks(recommendations, state, unresolved_drafted=2)

    assert result["status"] == "degraded"
    assert any("stale" in warning.lower() for warning in result["uncertainties"])
    assert any("team count" in warning.lower() for warning in result["uncertainties"])
    assert any("identit" in warning.lower() for warning in result["uncertainties"])
    assert any(
        pair["nextTurnAvailabilityProbability"] is None
        for pair in result["combinations"]
        if pair["nextTurn"]["name"] == "Needed Runner"
    )
