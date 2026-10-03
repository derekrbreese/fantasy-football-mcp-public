"""Lineup slot filling against real league configurations (issue #14)."""

import pytest

from lineup_optimizer import LineupOptimizer, Player, parse_roster_slots
from src.parsers.yahoo_parsers import _extract_positions

# Starting slots of a 12-team league with 3 WR, a W/R flex and one IDP slot.
THREE_WR_IDP = [("QB", 1), ("WR", 3), ("RB", 2), ("TE", 1), ("W/R", 1), ("K", 1), ("DEF", 1), ("D", 1)]


def player(name, display, slot, score, status="OK", eligible=None):
    raw = {"display_position": display}
    if eligible is not None:
        raw["eligible_positions"] = eligible
    return Player(
        name=name, position=slot, team="X", status=status, composite_score=score, raw=raw
    )


def roster():
    # Current Yahoo slots are deliberately wrong: the optimizer must ignore them.
    return [
        player("Purdy", "QB", "QB", 20.1),
        player("Warren", "RB", "RB", 18.6),
        player("Skattebo", "RB", "RB", 15.4),
        player("Williams K", "RB", "W/R", 13.6),
        player("Stevenson", "RB", "BN", 12.7),
        player("Chase", "WR", "WR", 18.6),
        player("Addison", "WR", "WR", 12.9),
        player("McLaurin", "WR", "BN", 11.0, status="D"),
        player("Raymond", "WR", "WR", 9.7),
        player("Williams A", "WR", "BN", 5.9),
        player("Sadiq", "TE", "TE", 10.9, status="Q"),
        player("Goedert", "TE", "BN", 0.0, status="O"),
        player("Brown", "WR", "IR", 0.0, status="IR"),
        player("Bates", "K", "K", 7.7),
        player("Seahawks", "DEF", "DEF", 8.8),
        player("Oluokun", "LB", "D", 6.0),
    ]


async def build(players, slots=THREE_WR_IDP, strategy="balanced"):
    return await LineupOptimizer().optimize_lineup_smart(players, strategy, roster_slots=slots)


@pytest.mark.asyncio
async def test_every_starting_slot_is_filled():
    result = await build(roster())
    assert list(result["starters"]) == ["QB", "WR1", "WR2", "WR3", "RB1", "RB2", "TE", "W/R", "K", "DEF", "D"]
    assert not [e for e in result["errors"] if "can fill" in e]


@pytest.mark.asyncio
async def test_bench_players_can_start_and_flex_takes_best_remaining():
    starters = (await build(roster()))["starters"]
    assert starters["RB1"].name == "Warren"
    assert starters["RB2"].name == "Skattebo"
    # Kyren sat in the W/R slot and Stevenson on the bench; both outrank Raymond.
    assert starters["W/R"].name == "Williams K"
    assert {starters[k].name for k in ("WR1", "WR2", "WR3")} == {"Chase", "Addison", "McLaurin"}
    assert starters["D"].name == "Oluokun"


@pytest.mark.asyncio
async def test_unavailable_players_do_not_start():
    players = [p for p in roster() if p.name != "Sadiq"]  # leaves only Goedert (Out) at TE
    result = await build(players)
    names = {p.name for p in result["starters"].values()}
    assert "Brown" not in names
    # Goedert is the only TE, so he fills the slot, with a warning rather than silently.
    assert result["starters"]["TE"].name == "Goedert"
    assert "Goedert starts at TE but is listed O." in result["errors"]


@pytest.mark.asyncio
async def test_forced_bye_week_starter_is_reported_as_on_bye():
    lone_te = player("Kincaid", "TE", "TE", 0.0)
    lone_te.on_bye = True
    players = [p for p in roster() if p.raw["display_position"] != "TE"] + [lone_te]
    assert "Kincaid starts at TE but is on bye." in (await build(players))["errors"]


@pytest.mark.asyncio
async def test_wrt_flex_accepts_tight_ends_but_wr_flex_does_not():
    # Two TEs who both outscore every remaining RB/WR: the second one is flex-worthy.
    players = roster() + [player("Kittle", "TE", "BN", 15.0), player("Kelce", "TE", "BN", 14.0)]
    wr_flex = (await build(players))["starters"]
    assert wr_flex["TE"].name == "Kittle"
    assert wr_flex["W/R"].name == "Williams K"

    wrt = [s if s[0] != "W/R" else ("W/R/T", 1) for s in THREE_WR_IDP]
    assert (await build(players, wrt))["starters"]["W/R/T"].name == "Kelce"


@pytest.mark.asyncio
async def test_yahoo_eligible_positions_are_honored():
    # A WR/TE dual-eligible player can fill TE when Yahoo says so.
    players = [p for p in roster() if p.status != "Q"] + [
        player("Hybrid", "WR,TE", "BN", 9.0, eligible=["WR", "TE", "W/R"])
    ]
    assert (await build(players))["starters"]["TE"].name == "Hybrid"


@pytest.mark.asyncio
async def test_default_slots_when_settings_missing():
    result = await LineupOptimizer().optimize_lineup_smart(roster(), "balanced")
    assert "W/R/T" in result["starters"]
    assert any("assumed a standard lineup" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_strategy_changes_the_score_used():
    a = player("Steady", "WR", "WR", 10.0)
    a.floor_projection, a.ceiling_projection = 9.0, 11.0
    b = player("Boom", "WR", "WR", 10.0)
    b.floor_projection, b.ceiling_projection = 4.0, 18.0
    slots = [("WR", 1)]
    assert (await build([a, b], slots, "conservative"))["starters"]["WR"].name == "Steady"
    assert (await build([a, b], slots, "aggressive"))["starters"]["WR"].name == "Boom"


def test_parse_roster_slots_from_yahoo_settings_payload():
    payload = {
        "fantasy_content": {
            "league": [
                {"league_key": "470.l.1"},
                {
                    "settings": [
                        {
                            "roster_positions": [
                                {"roster_position": {"position": "QB", "count": 1, "is_starting_position": 1}},
                                {"roster_position": {"position": "WR", "count": 3, "is_starting_position": 1}},
                                {"roster_position": {"position": "W/R", "count": 1, "is_starting_position": 1}},
                                {"roster_position": {"position": "D", "count": 1, "is_starting_position": 1}},
                                {"roster_position": {"position": "BN", "count": 6, "is_starting_position": 0}},
                                {"roster_position": {"position": "IR", "count": 2, "is_starting_position": 0}},
                            ]
                        }
                    ]
                },
            ]
        }
    }
    assert parse_roster_slots(payload) == [("QB", 1), ("WR", 3), ("W/R", 1), ("D", 1)]


def test_parse_roster_slots_handles_aliases_and_plain_lists():
    assert parse_roster_slots([{"position": "DST", "count": 1}, {"position": "FLEX", "count": 2}]) == [
        ("DEF", 1),
        ("W/R/T", 2),
    ]
    assert parse_roster_slots({}) == []


@pytest.mark.parametrize(
    "raw",
    [
        [{"position": "WR"}, {"position": "W/R/T"}],
        {"position": ["WR", "W/R/T"]},
        {"0": {"position": "WR"}, "1": {"position": "W/R/T"}, "count": 2},
    ],
)
def test_extract_positions_shapes(raw):
    assert _extract_positions(raw) == ["WR", "W/R/T"]
