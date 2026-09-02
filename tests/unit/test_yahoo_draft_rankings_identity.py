"""Yahoo draft rankings retain only canonical player keys."""

import pytest

import fantasy_football_multi_league as yahoo_server


def ranking_response(player_key: object) -> dict:
    return {
        "fantasy_content": {
            "league": [
                {},
                {
                    "players": {
                        "0": {
                            "player": [
                                [
                                    {"player_key": player_key},
                                    {"name": {"full": "Breece Hall"}},
                                    {"display_position": "RB"},
                                    {"editorial_team_abbr": "NYJ"},
                                ]
                            ]
                        },
                        "count": 1,
                    }
                },
            ]
        }
    }


@pytest.mark.asyncio
async def test_draft_rankings_retain_a_canonical_yahoo_player_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(_endpoint: str) -> dict:
        return ranking_response("461.p.33536")

    monkeypatch.setattr(yahoo_server, "yahoo_api_call", fake_call)

    result = await yahoo_server.get_draft_rankings("461.l.61410")

    assert result[0]["player_key"] == "461.p.33536"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "player_key",
    [
        "https://evil.test/?player_key=461.p.33536&auth=secret",
        "nfl.p.33536",
    ],
)
async def test_draft_rankings_drop_malformed_or_url_bearing_player_keys(
    player_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call(_endpoint: str) -> dict:
        return ranking_response(player_key)

    monkeypatch.setattr(yahoo_server, "yahoo_api_call", fake_call)

    result = await yahoo_server.get_draft_rankings("461.l.61410")

    assert "player_key" not in result[0]
    assert "evil.test" not in str(result)
