"""Strict Yahoo player-key validation shared by private server boundaries."""

import pytest

from src.services.yahoo_player_identity import normalize_yahoo_player_key


@pytest.mark.parametrize("value", ["461.p.33536", "449.p.100042"])
def test_accepts_canonical_yahoo_player_keys(value: str) -> None:
    assert normalize_yahoo_player_key(f" {value} ") == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        33536,
        "",
        "p.33536",
        "461.p.0",
        "461.player.33536",
        "nfl.p.30123",
        "461.p.33536?auth=secret",
        "https://football.fantasysports.yahoo.com/?player_key=461.p.33536",
        "461.p.33536/extra",
    ],
)
def test_rejects_partial_or_url_bearing_player_keys(value: object) -> None:
    assert normalize_yahoo_player_key(value) is None
