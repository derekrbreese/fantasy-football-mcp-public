import json
import stat
from pathlib import Path

import pytest

from src.services.live_draft_store import (
    LiveDraftValidationError,
    load_live_draft,
    save_live_draft,
)


def draft_context(league_id: str = "10462193") -> dict:
    return {
        "schemaVersion": 1,
        "source": "yahoo-draft-recorder",
        "generatedAt": "2026-08-31T22:45:00.000Z",
        "draft": {
            "sport": "f1",
            "leagueId": league_id,
            "teamId": "6",
            "sessionKey": f"f1:{league_id}",
            "updatedAt": "2026-08-31T22:44:58.255Z",
        },
        "summary": {
            "totalPicks": 2,
            "latestOverallPick": 19,
            "nextOverallPick": 20,
            "userPickCount": 1,
        },
        "userRoster": [
            {
                "pickNumber": 19,
                "player": "S. Barkley",
                "position": "RB",
                "nflTeam": "PHI",
                "fantasyTeam": "Your Team",
                "isUserPick": True,
            }
        ],
        "teamRosters": {},
        "picks": [
            {
                "pickNumber": 1,
                "player": "J. Gibbs",
                "position": "RB",
                "nflTeam": "DET",
                "fantasyTeam": "Team 1",
                "isUserPick": False,
            },
            {
                "pickNumber": 19,
                "roundNumber": 2,
                "player": "S. Barkley",
                "position": "RB",
                "nflTeam": "PHI",
                "fantasyTeam": "Your Team",
                "isUserPick": True,
            },
        ],
    }


def test_saves_and_loads_latest_live_draft_context(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    first = draft_context("111")
    second = draft_context("222")
    second["generatedAt"] = "2026-08-31T22:46:00.000Z"

    save_live_draft(first, path)
    save_live_draft(second, path)

    assert load_live_draft(path=path)["draft"]["leagueId"] == "222"
    assert load_live_draft("111", path=path)["draft"]["leagueId"] == "111"
    assert json.loads(path.read_text())["f1:222"]["summary"]["totalPicks"] == 2


def test_creates_private_store_directory(tmp_path: Path) -> None:
    path = tmp_path / "private" / "live-drafts.json"

    save_live_draft(draft_context(), path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_strips_unknown_fields_including_credentials(tmp_path: Path) -> None:
    context = draft_context()
    context["auth"] = "top-secret"
    context["draft"]["url"] = "https://example.test/?auth=top-secret"
    context["picks"][0]["cookie"] = "top-secret"

    saved = save_live_draft(context, tmp_path / "live-drafts.json")

    assert "top-secret" not in json.dumps(saved)
    assert "auth" not in saved
    assert "url" not in saved["draft"]
    assert "cookie" not in saved["picks"][0]


def test_rejects_invalid_or_oversized_context(tmp_path: Path) -> None:
    with pytest.raises(LiveDraftValidationError):
        save_live_draft({"draft": {}, "picks": []}, tmp_path / "live-drafts.json")

    context = draft_context()
    context["picks"] = context["picks"] * 300
    with pytest.raises(LiveDraftValidationError, match="500"):
        save_live_draft(context, tmp_path / "live-drafts.json")


def test_ignores_stale_snapshot_that_would_roll_draft_backward(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    newest = draft_context()
    newest["generatedAt"] = "2026-08-31T22:50:00.000Z"
    newest["picks"].append(
        {
            "pickNumber": 20,
            "player": "C. Lamb",
            "position": "WR",
            "nflTeam": "DAL",
            "fantasyTeam": "Team 2",
            "isUserPick": False,
        }
    )
    stale = draft_context()
    stale["generatedAt"] = "2026-08-31T22:55:00.000Z"

    save_live_draft(newest, path)
    with pytest.raises(LiveDraftValidationError, match="stale"):
        save_live_draft(stale, path)

    assert load_live_draft(path=path)["generatedAt"] == newest["generatedAt"]
    assert load_live_draft(path=path)["summary"]["latestOverallPick"] == 20


def test_load_latest_compares_session_times_as_instants(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    first = draft_context("111")
    first["generatedAt"] = "2026-08-31T22:50:00Z"
    later_with_offset = draft_context("222")
    later_with_offset["generatedAt"] = "2026-08-31T18:51:00-04:00"

    save_live_draft(first, path)
    save_live_draft(later_with_offset, path)

    assert load_live_draft(path=path)["draft"]["leagueId"] == "222"


def test_compares_snapshot_times_as_instants_not_strings(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    first = draft_context()
    first["generatedAt"] = "2026-08-31T22:50:00Z"
    later_with_offset = draft_context()
    later_with_offset["generatedAt"] = "2026-08-31T18:51:00-04:00"

    save_live_draft(first, path)
    saved = save_live_draft(later_with_offset, path)

    assert saved["generatedAt"] == later_with_offset["generatedAt"]
