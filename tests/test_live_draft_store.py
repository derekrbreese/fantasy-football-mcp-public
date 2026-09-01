import json
import stat
from pathlib import Path

import pytest

import src.services.live_draft_store as live_draft_store
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


def test_rejects_ambiguous_session_lookup_for_same_league_id(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    first = draft_context("111")
    second = draft_context("111")
    second["draft"]["sport"] = "nfl"
    second["draft"]["sessionKey"] = "nfl:111"
    second["generatedAt"] = "2026-08-31T22:46:00.000Z"
    save_live_draft(first, path)
    save_live_draft(second, path)

    with pytest.raises(LiveDraftValidationError, match="ambiguous"):
        load_live_draft("111", path=path, reject_ambiguous=True)


def test_creates_private_store_directory(tmp_path: Path) -> None:
    path = tmp_path / "private" / "live-drafts.json"

    save_live_draft(draft_context(), path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_tightens_existing_default_store_directory_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / ".fantasy-football-mcp"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    path = parent / "live-drafts.json"
    monkeypatch.delenv("FANTASY_FOOTBALL_LIVE_DRAFT_PATH", raising=False)
    monkeypatch.setattr(live_draft_store, "DEFAULT_STORE_PATH", path)

    save_live_draft(draft_context())

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_does_not_chmod_existing_shared_parent_for_custom_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_parent = tmp_path / "shared"
    shared_parent.mkdir(mode=0o755)
    shared_parent.chmod(0o755)
    path = shared_parent / "custom-live-drafts.json"
    monkeypatch.setenv("FANTASY_FOOTBALL_LIVE_DRAFT_PATH", str(path))

    save_live_draft(draft_context())

    assert stat.S_IMODE(shared_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


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


def test_accepts_newer_verified_authoritative_downward_repair(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    existing = draft_context()
    existing["generatedAt"] = "2026-08-31T22:50:00.000Z"
    existing["picks"] = [
        {
            "pickNumber": pick_number,
            "player": f"Player {pick_number}",
            "fantasyTeam": "Team 1",
            "isUserPick": False,
        }
        for pick_number in range(1, 21)
    ]
    repair = draft_context()
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["repair"] = True
    repair["picks"] = existing["picks"][:12]

    save_live_draft(existing, path)
    saved = save_live_draft(repair, path)

    assert "repair" not in saved
    assert saved["summary"]["latestOverallPick"] == 12
    assert [pick["pickNumber"] for pick in saved["picks"]] == list(range(1, 13))


@pytest.mark.parametrize(
    "pick_numbers, message",
    [
        ([1, 3], "contiguous"),
        ([1, 2, 2], "unique"),
        ([1, None], "positively numbered"),
    ],
)
def test_rejects_invalid_authoritative_repair(
    tmp_path: Path, pick_numbers: list[int | None], message: str
) -> None:
    path = tmp_path / "live-drafts.json"
    existing = draft_context()
    existing["picks"] = [
        {
            "pickNumber": pick_number,
            "player": f"Existing {pick_number}",
            "fantasyTeam": "Team 1",
            "isUserPick": False,
        }
        for pick_number in range(1, 5)
    ]
    repair = draft_context()
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["repair"] = True
    repair["picks"] = [
        {
            **({"pickNumber": pick_number} if pick_number is not None else {}),
            "player": f"Repair {pick_number}",
            "fantasyTeam": "Team 1",
            "isUserPick": False,
        }
        for pick_number in pick_numbers
    ]

    save_live_draft(existing, path)
    with pytest.raises(LiveDraftValidationError, match=message):
        save_live_draft(repair, path)

    assert load_live_draft(path=path)["summary"]["latestOverallPick"] == 4


def test_rejects_repair_that_is_not_newer(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    existing = draft_context()
    existing["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair = draft_context()
    repair["repair"] = True
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["picks"] = [repair["picks"][0]]

    save_live_draft(existing, path)
    with pytest.raises(LiveDraftValidationError, match="newer"):
        save_live_draft(repair, path)


def test_identical_repair_retry_succeeds_without_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live-drafts.json"
    existing = draft_context()
    existing["picks"] = [
        {
            "pickNumber": pick_number,
            "player": f"Existing {pick_number}",
            "fantasyTeam": "Team 1",
            "isUserPick": False,
        }
        for pick_number in range(1, 5)
    ]
    repair = draft_context()
    repair["repair"] = True
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["picks"] = existing["picks"][:2]

    save_live_draft(existing, path)
    first_saved = save_live_draft(repair, path)

    def fail_if_rewritten(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("identical repair retry must not rewrite the store")

    monkeypatch.setattr(live_draft_store.os, "replace", fail_if_rewritten)

    assert save_live_draft(repair, path) == first_saved


def test_rejects_equal_timestamp_divergent_repair(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    repair = draft_context()
    repair["repair"] = True
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["picks"] = [repair["picks"][0]]
    divergent = draft_context()
    divergent["repair"] = True
    divergent["generatedAt"] = repair["generatedAt"]
    divergent["picks"] = [{**repair["picks"][0], "player": "Different Player"}]

    save_live_draft(repair, path)
    with pytest.raises(LiveDraftValidationError, match="newer"):
        save_live_draft(divergent, path)

    assert load_live_draft(path=path)["picks"][0]["player"] != "Different Player"


def test_rejects_empty_authoritative_repair(tmp_path: Path) -> None:
    context = draft_context()
    context["repair"] = True
    context["picks"] = []

    with pytest.raises(LiveDraftValidationError, match="at least one pick"):
        save_live_draft(context, tmp_path / "live-drafts.json")


def test_rejects_repair_for_different_saved_draft_identity(tmp_path: Path) -> None:
    path = tmp_path / "live-drafts.json"
    existing = draft_context()
    existing["draft"]["teamId"] = "6"
    repair = draft_context()
    repair["repair"] = True
    repair["generatedAt"] = "2026-08-31T22:55:00.000Z"
    repair["draft"]["teamId"] = "7"
    repair["picks"] = [repair["picks"][0]]

    save_live_draft(existing, path)
    with pytest.raises(LiveDraftValidationError, match="identity"):
        save_live_draft(repair, path)


def test_rejects_mismatched_session_identity(tmp_path: Path) -> None:
    context = draft_context()
    context["draft"]["sessionKey"] = "f1:someone-else"

    with pytest.raises(LiveDraftValidationError, match="sport:leagueId"):
        save_live_draft(context, tmp_path / "live-drafts.json")


def test_allowlists_boolean_repair_marker_only(tmp_path: Path) -> None:
    context = draft_context()
    context["repair"] = True
    context["repairMetadata"] = {
        "url": "https://example.test/?auth=top-secret",
        "reason": "top-secret",
    }
    context["picks"] = [context["picks"][0]]

    saved = save_live_draft(context, tmp_path / "live-drafts.json")

    assert "repair" not in saved
    assert "repairMetadata" not in saved
    assert "top-secret" not in json.dumps(saved)

    context["repair"] = {"requested": True}
    with pytest.raises(LiveDraftValidationError, match="repair must be a boolean"):
        save_live_draft(context, tmp_path / "other-live-drafts.json")


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
