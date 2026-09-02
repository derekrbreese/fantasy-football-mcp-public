import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

import src.services.local_draft_profile_store as profile_store
from src.services.local_draft_profile_store import (
    LocalDraftProfileValidationError,
    bind_default_local_draft_profile,
    clear_default_local_draft_profile,
    list_local_draft_profile_defaults,
    load_default_local_draft_profile,
    load_local_draft_profile,
    local_draft_profile_revision,
    profile_from_draftsheets_rows,
    profile_from_draftsheets_xlsx,
    sanitize_local_draft_profile,
    save_local_draft_profile,
    set_default_local_draft_profile,
)


def draft_identity(league_id: str = "498589", team_id: str = "6") -> dict:
    return {
        "sport": "nfl",
        "leagueId": league_id,
        "teamId": team_id,
        "sessionKey": f"nfl:{league_id}",
    }


def local_profile(league_id: str = "498589", team_id: str = "6") -> dict:
    return {
        "schemaVersion": 1,
        "source": "local-draft-profile",
        "season": 2026,
        "importedAt": "2026-09-01T16:45:00-07:00",
        "draft": draft_identity(league_id, team_id),
        "rankings": [
            {
                "name": " Jahmyr\u00a0Gibbs ",
                "position": "rb1",
                "team": "det",
                "rank": 1,
                "average_draft_position": 1.5,
                "bye_week": 6,
                "notes": "must not be retained",
            },
            {
                "name": "Seattle Seahawks",
                "position": "D/ST1",
                "team": "sea",
                "rank": 2,
            },
        ],
        "leagueSettings": {
            "teams": 12,
            "rosterPositions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "W/R/T", "count": 1},
                {"position": "K", "count": 1},
                {"position": "DEF", "count": 1},
                {"position": "BENCH", "count": 6},
                {"position": "IR", "count": 1},
            ],
            "scoring": {"secret": "not retained"},
        },
        "provenance": {
            "kind": "user-import",
            "format": "draftsheets-2026",
            "asOf": "2026-08-31",
            "url": "https://example.test/?token=secret",
        },
        "credentials": "secret",
    }


def local_profile_for_season(
    season: int, league_id: str = "498589", team_id: str = "6"
) -> dict:
    profile = local_profile(league_id, team_id)
    profile["season"] = season
    profile["importedAt"] = f"{season}-09-01T16:45:00-07:00"
    profile["provenance"]["asOf"] = f"{season}-08-31"
    return profile


def test_sanitizes_canonical_profile_and_strips_unknown_fields() -> None:
    sanitized = sanitize_local_draft_profile(local_profile())

    assert sanitized == {
        "schemaVersion": 1,
        "source": "local-draft-profile",
        "season": 2026,
        "importedAt": "2026-09-01T23:45:00Z",
        "draft": draft_identity(),
        "rankings": [
            {
                "name": "Jahmyr Gibbs",
                "position": "RB",
                "team": "DET",
                "rank": 1,
                "average_draft_position": 1.5,
                "bye_week": 6,
            },
            {
                "name": "Seattle Seahawks",
                "position": "DST",
                "team": "SEA",
                "rank": 2,
            },
        ],
        "leagueSettings": {
            "teams": 12,
            "rosterPositions": [
                {"position": "QB", "count": 1},
                {"position": "RB", "count": 2},
                {"position": "WR", "count": 2},
                {"position": "TE", "count": 1},
                {"position": "FLEX", "count": 1},
                {"position": "K", "count": 1},
                {"position": "DST", "count": 1},
                {"position": "BN", "count": 6},
                {"position": "IR", "count": 1},
            ],
        },
        "provenance": {
            "kind": "user-import",
            "format": "draftsheets-2026",
            "asOf": "2026-08-31",
        },
    }
    assert "secret" not in json.dumps(sanitized)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(schemaVersion=2), "schemaVersion 1"),
        (lambda value: value.update(source="remote"), "source"),
        (lambda value: value.update(season=2025), "asOf year"),
        (
            lambda value: value["draft"].update(sessionKey="nfl:other"),
            "sport:leagueId",
        ),
        (lambda value: value["rankings"][0].update(position="FLEX"), "position"),
        (lambda value: value["rankings"][0].update(rank=True), "rank"),
        (lambda value: value["rankings"][0].update(team="https://bad"), "team"),
        (
            lambda value: value["provenance"].update(format="arbitrary-sheet"),
            "provenance.format",
        ),
    ],
)
def test_rejects_invalid_profile_fields(mutate: object, message: str) -> None:
    value = local_profile()
    mutate(value)  # type: ignore[operator]

    with pytest.raises(LocalDraftProfileValidationError, match=message):
        sanitize_local_draft_profile(value)


def test_rejects_duplicate_candidates_ranks_roster_slots_and_oversized_pool() -> None:
    duplicate_player = local_profile()
    duplicate_player["rankings"][1] = {
        **duplicate_player["rankings"][0],
        "rank": 2,
    }
    with pytest.raises(LocalDraftProfileValidationError, match="duplicate player"):
        sanitize_local_draft_profile(duplicate_player)

    duplicate_rank = local_profile()
    duplicate_rank["rankings"][1]["rank"] = 1
    with pytest.raises(LocalDraftProfileValidationError, match="duplicate rank"):
        sanitize_local_draft_profile(duplicate_rank)

    duplicate_slot = local_profile()
    duplicate_slot["leagueSettings"]["rosterPositions"].append({"position": "D/ST", "count": 1})
    with pytest.raises(LocalDraftProfileValidationError, match="duplicate roster"):
        sanitize_local_draft_profile(duplicate_slot)

    oversized = local_profile()
    oversized["rankings"] = [
        {
            "name": f"Player {rank}",
            "position": "WR",
            "team": "FA",
            "rank": rank,
        }
        for rank in range(1, 502)
    ]
    with pytest.raises(LocalDraftProfileValidationError, match="500"):
        sanitize_local_draft_profile(oversized)


def test_accepts_teamless_structured_candidate_without_inventing_team() -> None:
    profile = local_profile()
    profile["rankings"][0]["team"] = " \t "

    sanitized = sanitize_local_draft_profile(profile)

    assert "team" not in sanitized["rankings"][0]


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "https://example.test/player?token=secret",
        "Player?auth=secret",
        "cookie=session-secret",
        '=HYPERLINK("https://example.test/private")',
        "+cmd|' /C calc'!A0",
        "Player\x00Secret",
    ],
)
def test_rejects_url_auth_control_and_formula_like_player_names(
    unsafe_name: str,
) -> None:
    profile = local_profile()
    profile["rankings"][0]["name"] = unsafe_name

    with pytest.raises(LocalDraftProfileValidationError, match="player name is invalid"):
        sanitize_local_draft_profile(profile)


def test_saves_loads_exact_identity_and_isolates_leagues(tmp_path: Path) -> None:
    path = tmp_path / "private" / "draft-profiles.json"
    first = local_profile("111", "3")
    second = local_profile("222", "9")
    second["importedAt"] = "2026-09-01T16:46:00-07:00"

    save_local_draft_profile(first, path)
    save_local_draft_profile(second, path)

    assert load_local_draft_profile(draft_identity("111", "3"), path) == (
        sanitize_local_draft_profile(first)
    )
    assert load_local_draft_profile(draft_identity("222", "9"), path) == (
        sanitize_local_draft_profile(second)
    )
    assert load_local_draft_profile(draft_identity("111", "8"), path) is None
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_lists_privacy_minimal_profile_summaries(tmp_path: Path) -> None:
    path = tmp_path / "draft-profiles.json"
    save_local_draft_profile(local_profile("111", "3"), path)

    summaries = profile_store.list_local_draft_profile_summaries(path)

    assert summaries == [
        {
            "sport": "nfl",
            "leagueId": "111",
            "importedAt": "2026-09-01T23:45:00Z",
            "asOf": "2026-08-31",
            "format": "draftsheets-2026",
            "rankingCount": 2,
        }
    ]
    serialized = json.dumps(summaries)
    assert "teamId" not in serialized
    assert "sessionKey" not in serialized
    assert "Jahmyr" not in serialized
    assert "rankings" not in serialized


def test_explicit_bind_copies_only_profile_data_to_exact_target_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "draft-profiles.json"
    source = save_local_draft_profile(local_profile("111", "3"), path)
    target = draft_identity("222", "9")

    bound = profile_store.bind_local_draft_profile("111", target, path)

    assert bound["draft"] == target
    assert bound["rankings"] == source["rankings"]
    assert bound["leagueSettings"] == source["leagueSettings"]
    assert bound["provenance"] == source["provenance"]
    assert load_local_draft_profile(target, path) == bound
    assert load_local_draft_profile(draft_identity("111", "3"), path) == source
    assert "picks" not in json.dumps(bound)


def test_explicit_bind_rejects_missing_cross_sport_and_existing_target_profiles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "draft-profiles.json"
    save_local_draft_profile(local_profile("111", "3"), path)

    with pytest.raises(profile_store.LocalDraftProfileNotFoundError, match="not found"):
        profile_store.bind_local_draft_profile("999", draft_identity("222", "9"), path)

    cross_sport = draft_identity("222", "9")
    cross_sport.update(sport="f1", sessionKey="f1:222")
    with pytest.raises(profile_store.LocalDraftProfileConflictError, match="sport"):
        profile_store.bind_local_draft_profile("111", cross_sport, path)

    existing = local_profile("222", "9")
    existing["rankings"][0]["rank"] = 2
    existing["rankings"][1]["rank"] = 1
    save_local_draft_profile(existing, path)
    with pytest.raises(profile_store.LocalDraftProfileConflictError, match="different"):
        profile_store.bind_local_draft_profile("111", draft_identity("222", "9"), path)

    # Rebinding a profile with identical reusable content is idempotent.
    assert profile_store.bind_local_draft_profile(
        "111", draft_identity("111", "3"), path
    ) == load_local_draft_profile(draft_identity("111", "3"), path)


def test_sets_lists_loads_and_clears_one_default_per_sport(tmp_path: Path) -> None:
    profile_path = tmp_path / "private" / "draft-profiles.json"
    defaults_path = tmp_path / "private" / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), profile_path)

    selected = set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=profile_path,
        defaults_path=defaults_path,
    )

    assert selected == {"sport": "nfl", "sourceLeagueId": "111"}
    assert load_default_local_draft_profile("nfl", defaults_path) == selected
    assert load_default_local_draft_profile("f1", defaults_path) is None
    assert list_local_draft_profile_defaults(defaults_path) == [selected]
    assert json.loads(defaults_path.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "defaults": [selected],
    }
    assert stat.S_IMODE(defaults_path.stat().st_mode) == 0o600

    assert clear_default_local_draft_profile("nfl", defaults_path) is True
    assert clear_default_local_draft_profile("nfl", defaults_path) is False
    assert list_local_draft_profile_defaults(defaults_path) == []


def test_default_selection_rejects_missing_and_cross_sport_sources(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), profile_path)

    with pytest.raises(profile_store.LocalDraftProfileNotFoundError, match="not found"):
        set_default_local_draft_profile(
            "nfl",
            "999",
            profile_path=profile_path,
            defaults_path=defaults_path,
        )
    with pytest.raises(profile_store.LocalDraftProfileConflictError, match="sport"):
        set_default_local_draft_profile(
            "f1",
            "111",
            profile_path=profile_path,
            defaults_path=defaults_path,
        )
    assert not defaults_path.exists()


@pytest.mark.parametrize("source_season", [2025, 2027])
def test_default_selection_rejects_stale_and_future_source_seasons(
    tmp_path: Path, source_season: int
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(
        local_profile_for_season(source_season, "111", "3"), profile_path
    )

    with pytest.raises(
        profile_store.LocalDraftProfileConflictError,
        match=rf"season {source_season}.*current UTC season 2026",
    ):
        set_default_local_draft_profile(
            "nfl",
            "111",
            profile_path=profile_path,
            defaults_path=defaults_path,
            current_season=2026,
        )

    assert not defaults_path.exists()


@pytest.mark.parametrize("source_season", [2025, 2027])
def test_default_bind_rejects_stale_and_future_source_seasons_without_writing(
    tmp_path: Path, source_season: int
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(
        local_profile_for_season(source_season, "111", "3"), profile_path
    )
    set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=profile_path,
        defaults_path=defaults_path,
        current_season=source_season,
    )
    target = draft_identity("222", "9")

    with pytest.raises(
        profile_store.LocalDraftProfileConflictError,
        match=rf"season {source_season}.*current UTC season 2026",
    ):
        bind_default_local_draft_profile(
            target,
            profile_path=profile_path,
            defaults_path=defaults_path,
            current_season=2026,
        )

    assert load_local_draft_profile(target, profile_path) is None


def test_default_bind_is_exact_idempotent_and_never_copies_picks(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    source = save_local_draft_profile(local_profile("111", "3"), profile_path)
    set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=profile_path,
        defaults_path=defaults_path,
    )
    target = draft_identity("222", "9")

    bound = bind_default_local_draft_profile(
        target,
        profile_path=profile_path,
        defaults_path=defaults_path,
    )

    assert bound is not None
    assert bound["draft"] == target
    assert bound["rankings"] == source["rankings"]
    assert bound["leagueSettings"] == source["leagueSettings"]
    assert "picks" not in json.dumps(bound)
    assert bind_default_local_draft_profile(
        target,
        profile_path=profile_path,
        defaults_path=defaults_path,
    ) == bound

    no_default_target = draft_identity("333", "7")
    clear_default_local_draft_profile("nfl", defaults_path)
    assert bind_default_local_draft_profile(
        no_default_target,
        profile_path=profile_path,
        defaults_path=defaults_path,
    ) is None


def test_orphaned_default_source_fails_closed_without_writing_target(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), profile_path)
    set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=profile_path,
        defaults_path=defaults_path,
    )
    profile_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(profile_store.LocalDraftProfileNotFoundError, match="source"):
        bind_default_local_draft_profile(
            draft_identity("222", "9"),
            profile_path=profile_path,
            defaults_path=defaults_path,
        )

    assert json.loads(profile_path.read_text(encoding="utf-8")) == {}


def test_existing_exact_profile_wins_over_a_different_default(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), profile_path)
    existing = local_profile("222", "9")
    existing["importedAt"] = "2026-09-01T16:46:00-07:00"
    existing["rankings"][0]["name"] = "Existing Exact Player"
    saved_existing = save_local_draft_profile(existing, profile_path)
    set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=profile_path,
        defaults_path=defaults_path,
    )

    assert bind_default_local_draft_profile(
        draft_identity("222", "9"),
        profile_path=profile_path,
        defaults_path=defaults_path,
        current_season=2027,
    ) == saved_existing
    assert load_local_draft_profile(
        draft_identity("222", "9"), profile_path
    ) == saved_existing


def test_default_store_permissions_and_custom_parent_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_profile_path = tmp_path / "private" / "draft-profiles.json"
    private_defaults_path = tmp_path / "private" / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), private_profile_path)
    set_default_local_draft_profile(
        "nfl",
        "111",
        profile_path=private_profile_path,
        defaults_path=private_defaults_path,
    )
    assert stat.S_IMODE(private_defaults_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(private_defaults_path.stat().st_mode) == 0o600

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    profile_path = shared / "profiles.json"
    defaults_path = shared / "defaults.json"
    save_local_draft_profile(local_profile("222", "4"), profile_path)
    monkeypatch.setenv(
        "FANTASY_FOOTBALL_DRAFT_PROFILE_DEFAULTS_PATH", str(defaults_path)
    )
    set_default_local_draft_profile("nfl", "222", profile_path=profile_path)

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE(defaults_path.stat().st_mode) == 0o600


def test_default_store_tightens_app_directory_and_rejects_default_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FANTASY_FOOTBALL_DRAFT_PROFILE_PATH", raising=False)
    monkeypatch.delenv(
        "FANTASY_FOOTBALL_DRAFT_PROFILE_DEFAULTS_PATH", raising=False
    )
    parent = tmp_path / ".fantasy-football-mcp"
    profile_path = parent / "draft-profiles.json"
    defaults_path = parent / "draft-profile-defaults.json"
    monkeypatch.setattr(profile_store, "DEFAULT_PROFILE_STORE_PATH", profile_path)
    monkeypatch.setattr(
        profile_store, "DEFAULT_PROFILE_DEFAULTS_STORE_PATH", defaults_path
    )
    save_local_draft_profile(local_profile("111", "3"))
    parent.chmod(0o755)

    set_default_local_draft_profile("nfl", "111")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(defaults_path.stat().st_mode) == 0o600

    real_parent = tmp_path / "real"
    real_profile_path = real_parent / "draft-profiles.json"
    save_local_draft_profile(local_profile("222", "4"), real_profile_path)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    monkeypatch.setattr(
        profile_store,
        "DEFAULT_PROFILE_STORE_PATH",
        linked_parent / "draft-profiles.json",
    )
    monkeypatch.setattr(
        profile_store,
        "DEFAULT_PROFILE_DEFAULTS_STORE_PATH",
        linked_parent / "draft-profile-defaults.json",
    )

    with pytest.raises(LocalDraftProfileValidationError, match="symbolic link"):
        set_default_local_draft_profile("nfl", "222")


def test_default_store_atomic_failure_preserves_existing_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path = tmp_path / "draft-profiles.json"
    defaults_path = tmp_path / "draft-profile-defaults.json"
    save_local_draft_profile(local_profile("111", "3"), profile_path)
    second = local_profile("222", "4")
    second["importedAt"] = "2026-09-01T16:46:00-07:00"
    save_local_draft_profile(second, profile_path)
    set_default_local_draft_profile(
        "nfl", "111", profile_path=profile_path, defaults_path=defaults_path
    )
    original = defaults_path.read_bytes()

    monkeypatch.setattr(
        profile_store.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure")),
    )
    with pytest.raises(OSError, match="simulated"):
        set_default_local_draft_profile(
            "nfl", "222", profile_path=profile_path, defaults_path=defaults_path
        )

    assert defaults_path.read_bytes() == original
    assert not list(defaults_path.parent.glob(".draft-profile-defaults-*.json"))


def test_rejects_cross_team_overwrite_and_stale_import(tmp_path: Path) -> None:
    path = tmp_path / "draft-profiles.json"
    saved = local_profile("111", "3")
    save_local_draft_profile(saved, path)

    cross_team = local_profile("111", "4")
    cross_team["importedAt"] = "2026-09-01T16:50:00-07:00"
    with pytest.raises(LocalDraftProfileValidationError, match="identity"):
        save_local_draft_profile(cross_team, path)

    stale = local_profile("111", "3")
    stale["importedAt"] = "2026-09-01T16:44:00-07:00"
    with pytest.raises(LocalDraftProfileValidationError, match="newer"):
        save_local_draft_profile(stale, path)

    assert load_local_draft_profile(draft_identity("111", "3"), path) == (
        sanitize_local_draft_profile(saved)
    )


def test_identical_import_retry_is_idempotent_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "draft-profiles.json"
    profile = local_profile()
    expected = save_local_draft_profile(profile, path)

    def fail_if_rewritten(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("identical retry must not rewrite the store")

    monkeypatch.setattr(profile_store.os, "replace", fail_if_rewritten)

    assert save_local_draft_profile(profile, path) == expected


def test_identical_retry_restores_private_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "draft-profiles.json"
    profile = local_profile()
    save_local_draft_profile(profile, path)
    path.chmod(0o644)

    save_local_draft_profile(profile, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_custom_store_does_not_chmod_shared_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    path = parent / "profiles.json"
    monkeypatch.setenv("FANTASY_FOOTBALL_DRAFT_PROFILE_PATH", str(path))

    save_local_draft_profile(local_profile())

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_default_directory_is_private_and_symlinks_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / ".fantasy-football-mcp"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    path = parent / "draft-profiles.json"
    monkeypatch.delenv("FANTASY_FOOTBALL_DRAFT_PROFILE_PATH", raising=False)
    monkeypatch.setattr(profile_store, "DEFAULT_PROFILE_STORE_PATH", path)

    save_local_draft_profile(local_profile())

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700

    other = tmp_path / "other"
    other.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(other, target_is_directory=True)
    monkeypatch.setattr(
        profile_store,
        "DEFAULT_PROFILE_STORE_PATH",
        linked_parent / "draft-profiles.json",
    )
    with pytest.raises(LocalDraftProfileValidationError, match="symbolic link"):
        save_local_draft_profile(local_profile())

    with pytest.raises(LocalDraftProfileValidationError, match="symbolic link"):
        load_local_draft_profile(draft_identity())


def test_converts_allowlisted_draftsheets_rows_to_profile() -> None:
    ecr_rows = [
        {
            "RK": "2",
            "PLAYER NAME": "Seattle Seahawks",
            "TEAM": "sea",
            "POS": "DEF1",
            "BYE WEEK": "8",
            "ECR VS. ADP": "-5",
            "URL": "https://example.test/?token=secret",
        },
        {
            "RK": "1",
            "PLAYER NAME": "Jahmyr Gibbs",
            "TEAM": "det",
            "POS": "RB1",
            "BYE WEEK": 6.0,
            "ADP": "1.5",
        },
        {
            "RK": "3",
            "PLAYER NAME": "Free Agent Player",
            "TEAM": "FA",
            "POS": "WR1",
            "BYE WEEK": "-",
        },
        {"RK": None, "PLAYER NAME": None, "TEAM": None, "POS": None},
    ]
    scoring_rows = [
        {
            "#TEAMS:": "12",
            "QB:": 1.0,
            "RB:": "2",
            "WR:": 2,
            "TE:": 1,
            "FLEX:": 1,
            "BENCH:": 6,
            "SUPERFLEX:": 0,
        },
        {"K:": 1, "D/ST:": 1, "IR:": 1, "URL": "https://bad"},
    ]

    profile = profile_from_draftsheets_rows(
        ecr_rows,
        scoring_rows,
        draft=draft_identity(),
        imported_at="2026-09-01T16:45:00-07:00",
        season=2026,
        as_of="2026-08-31",
    )

    assert profile["rankings"] == [
        {
            "name": "Jahmyr Gibbs",
            "position": "RB",
            "team": "DET",
            "rank": 1,
            "average_draft_position": 1.5,
            "bye_week": 6,
        },
        {
            "name": "Seattle Seahawks",
            "position": "DST",
            "team": "SEA",
            "rank": 2,
            "bye_week": 8,
        },
        {
            "name": "Free Agent Player",
            "position": "WR",
            "team": "FA",
            "rank": 3,
        },
    ]
    assert "average_draft_position" not in profile["rankings"][1]
    assert profile["leagueSettings"] == {
        "teams": 12,
        "rosterPositions": [
            {"position": "QB", "count": 1},
            {"position": "RB", "count": 2},
            {"position": "WR", "count": 2},
            {"position": "TE", "count": 1},
            {"position": "FLEX", "count": 1},
            {"position": "K", "count": 1},
            {"position": "DST", "count": 1},
            {"position": "BN", "count": 6},
            {"position": "IR", "count": 1},
        ],
    }
    assert "secret" not in json.dumps(profile)


def test_converts_two_row_scoring_grid_and_bounds_candidates() -> None:
    ecr_rows = [
        {
            "RK": str(rank),
            "PLAYER NAME": f"Player {rank}",
            "TEAM": "FA",
            "POS": f"WR{rank}",
            "BYE WEEK": "",
        }
        for rank in range(501, 0, -1)
    ]
    scoring_rows = [
        {"I": "#TEAMS:", "J": "QB:", "K": "RB:", "L": "WR:"},
        {"I": 12, "J": 1, "K": 2, "L": 2},
        {"setting": "TE", "value": 1},
    ]

    profile = profile_from_draftsheets_rows(
        ecr_rows,
        scoring_rows,
        draft=draft_identity(),
        imported_at="2026-09-01T23:45:00Z",
        season=2026,
    )

    assert len(profile["rankings"]) == 500
    assert profile["rankings"][0]["rank"] == 1
    assert profile["rankings"][-1]["rank"] == 500
    assert profile["leagueSettings"]["teams"] == 12
    assert {item["position"] for item in profile["leagueSettings"]["rosterPositions"]} == {
        "QB",
        "RB",
        "WR",
        "TE",
    }


def _xlsx_bytes(
    *,
    include_required_sheets: bool = True,
    player_name: str = "Jahmyr Gibbs",
) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    scoring = workbook.active
    scoring.title = "Scoring" if include_required_sheets else "Wrong"
    scoring["B1"] = "2026-08-31"
    labels = ["#TEAMS:", "QB:", "RB:", "WR:", "TE:", "FLEX:", "BENCH:", "SUPERFLEX:"]
    values = [12, 1, 2, 2, 1, 1, 6, 0]
    for column, (label, value) in enumerate(zip(labels, values, strict=True), start=9):
        scoring.cell(row=3, column=column, value=label)
        scoring.cell(row=4, column=column, value=value)
    scoring["Q3"] = "K:"
    scoring["Q4"] = 1
    scoring["R3"] = "D/ST:"
    scoring["R4"] = 1
    scoring["S3"] = "IR:"
    scoring["S4"] = 1
    ecr = workbook.create_sheet("ECR")
    ecr.append(
        [
            "RK",
            "TIERS",
            "PLAYER NAME",
            "TEAM",
            "POS",
            "BYE WEEK",
            "UPSIDE",
            "BUST",
            "SOS SEASON",
            "ECR VS. ADP",
        ]
    )
    ecr.append([1, 1, player_name, "DET", "RB1", 6, "", "", "", -3])
    ecr.append([2, 1, "Seattle Seahawks", "SEA", "D/ST1", 8, "", "", "", 4])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_parses_bounded_draftsheets_xlsx_without_treating_delta_as_adp() -> None:
    profile = profile_from_draftsheets_xlsx(
        _xlsx_bytes(),
        draft=draft_identity(),
        imported_at="2026-09-01T23:45:00Z",
        season=2026,
    )

    assert [item["name"] for item in profile["rankings"]] == [
        "Jahmyr Gibbs",
        "Seattle Seahawks",
    ]
    assert all("average_draft_position" not in item for item in profile["rankings"])
    assert profile["provenance"]["asOf"] == "2026-08-31"
    slots = {
        item["position"]: item["count"] for item in profile["leagueSettings"]["rosterPositions"]
    }
    assert {name: slots[name] for name in ("K", "DST", "BN", "IR")} == {
        "K": 1,
        "DST": 1,
        "BN": 6,
        "IR": 1,
    }


def test_xlsx_roster_overrides_replace_conflicting_workbook_values() -> None:
    profile = profile_from_draftsheets_xlsx(
        _xlsx_bytes(),
        draft=draft_identity(),
        imported_at="2026-09-01T23:45:00Z",
        season=2026,
        roster_overrides={
            "QB": 1,
            "RB": 3,
            "WR": 3,
            "TE": 1,
            "FLEX": 2,
            "K": 1,
            "DST": 1,
            "BN": 5,
            "IR": 2,
        },
    )

    assert profile["leagueSettings"] == {
        "teams": 12,
        "rosterPositions": [
            {"position": "QB", "count": 1},
            {"position": "RB", "count": 3},
            {"position": "WR", "count": 3},
            {"position": "TE", "count": 1},
            {"position": "FLEX", "count": 2},
            {"position": "K", "count": 1},
            {"position": "DST", "count": 1},
            {"position": "BN", "count": 5},
            {"position": "IR", "count": 2},
        ],
    }


def test_xlsx_rejects_url_or_auth_parameter_disguised_as_player_name() -> None:
    for unsafe_name in (
        "https://example.test/player?token=secret",
        "Player?auth=secret",
    ):
        with pytest.raises(
            LocalDraftProfileValidationError,
            match="player name is invalid",
        ):
            profile_from_draftsheets_xlsx(
                _xlsx_bytes(player_name=unsafe_name),
                draft=draft_identity(),
                imported_at="2026-09-01T23:45:00Z",
                season=2026,
            )


def test_rejects_unsafe_or_wrong_xlsx_archives() -> None:
    with pytest.raises(LocalDraftProfileValidationError, match="ECR and Scoring"):
        profile_from_draftsheets_xlsx(
            _xlsx_bytes(include_required_sheets=False),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/vbaProject.bin", b"not-a-real-macro")
    with pytest.raises(LocalDraftProfileValidationError, match="macros"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink />")
    with pytest.raises(LocalDraftProfileValidationError, match="external links"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        with zipfile.ZipFile(io.BytesIO(_xlsx_bytes())) as clean:
            for member in clean.infolist():
                content = clean.read(member)
                if member.filename == "docProps/core.xml":
                    content = (
                        b'<?xml version="1.0"?>'
                        b'<!DOCTYPE properties [<!ENTITY secret "expanded">]>'
                        b'<properties>&secret;</properties>'
                    )
                archive.writestr(member, content)
    with pytest.raises(LocalDraftProfileValidationError, match="unsafe XML"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        with zipfile.ZipFile(io.BytesIO(_xlsx_bytes())) as clean:
            for member in clean.infolist():
                archive.writestr(member, clean.read(member))
        archive.writestr(
            "xl/macrosheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"/>',
        )
    with pytest.raises(LocalDraftProfileValidationError, match="macros"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        with zipfile.ZipFile(io.BytesIO(_xlsx_bytes())) as clean:
            for member in clean.infolist():
                archive.writestr(member, clean.read(member))
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/hyperlink" '
            'Target="https://example.test/?token=secret" TargetMode="External"/>'
            '</Relationships>',
        )
    with pytest.raises(LocalDraftProfileValidationError, match="external links"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )

    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(129):
            archive.writestr(f"part-{index}.xml", "")
    with pytest.raises(LocalDraftProfileValidationError, match="128"):
        profile_from_draftsheets_xlsx(
            source.getvalue(),
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )


def test_rejects_malformed_ecr_rows_instead_of_silently_importing() -> None:
    with pytest.raises(LocalDraftProfileValidationError, match="ECR row 1"):
        profile_from_draftsheets_rows(
            [{"RK": 1, "PLAYER NAME": "Player", "TEAM": "DET", "POS": None}],
            [{"TEAMS": 12, "QB": 1}],
            draft=draft_identity(),
            imported_at="2026-09-01T23:45:00Z",
            season=2026,
        )


def test_rejects_non_json_values_and_malformed_existing_store(tmp_path: Path) -> None:
    profile = local_profile()
    profile["rankings"][0]["rank"] = float("nan")
    with pytest.raises(LocalDraftProfileValidationError, match="rank"):
        sanitize_local_draft_profile(profile)

    path = tmp_path / "draft-profiles.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(LocalDraftProfileValidationError, match="malformed"):
        load_local_draft_profile(draft_identity(), path)


def test_atomic_failure_preserves_existing_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "draft-profiles.json"
    first = local_profile()
    save_local_draft_profile(first, path)
    original_bytes = path.read_bytes()
    updated = local_profile()
    updated["importedAt"] = "2026-09-01T16:50:00-07:00"
    updated["rankings"][0]["name"] = "Updated Player"

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(profile_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        save_local_draft_profile(updated, path)

    assert path.read_bytes() == original_bytes
    assert not list(path.parent.glob(".draft-profiles-*.json"))


def test_store_uses_configured_path_without_expanding_untrusted_profile_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured" / "profiles.json"
    monkeypatch.setenv("FANTASY_FOOTBALL_DRAFT_PROFILE_PATH", os.fspath(configured))
    profile = local_profile()
    profile["path"] = os.fspath(tmp_path / "attacker-controlled.json")

    save_local_draft_profile(profile)

    assert configured.exists()
    assert not (tmp_path / "attacker-controlled.json").exists()


def test_profile_revision_is_stable_and_content_sensitive() -> None:
    first = local_profile()
    reordered = dict(reversed(list(first.items())))
    changed = local_profile()
    changed["rankings"][0]["name"] = "Different Player"

    assert local_draft_profile_revision(first) == local_draft_profile_revision(reordered)
    assert local_draft_profile_revision(first) != local_draft_profile_revision(changed)


@pytest.mark.parametrize("format_name", ["draftsheets-2026", "csv", "json"])
def test_accepts_closed_profile_import_formats(format_name: str) -> None:
    profile = local_profile()
    profile["provenance"]["format"] = format_name

    assert sanitize_local_draft_profile(profile)["provenance"]["format"] == format_name
