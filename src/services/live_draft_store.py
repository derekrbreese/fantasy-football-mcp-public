"""Private local storage for live draft context posted by the browser extension."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

DEFAULT_STORE_PATH = Path.home() / ".fantasy-football-mcp" / "live-drafts.json"
MAX_PICKS = 500
_STORE_LOCK = threading.Lock()
_SESSION_KEY = re.compile(r"^[A-Za-z0-9_-]{1,32}:[A-Za-z0-9_-]{1,64}$")
_PICK_STRING_FIELDS = ("player", "position", "nflTeam", "fantasyTeam", "recordedAt")


class LiveDraftValidationError(ValueError):
    """Raised when extension-provided draft context is invalid."""


def _store_path(path: Optional[Union[str, Path]] = None) -> Path:
    configured = path or os.getenv("FANTASY_FOOTBALL_LIVE_DRAFT_PATH") or DEFAULT_STORE_PATH
    return Path(configured).expanduser()


def _safe_string(value: Any, field: str, maximum: int = 100) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveDraftValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise LiveDraftValidationError(f"{field} is too long")
    return result


def _safe_optional_string(value: Any, field: str, maximum: int = 100) -> Optional[str]:
    if value is None or value == "":
        return None
    return _safe_string(value, field, maximum)


def _safe_positive_integer(value: Any, field: str, maximum: int = 10000) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise LiveDraftValidationError(f"{field} must be a positive integer")
    return value


def _sanitize_pick(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveDraftValidationError("each pick must be an object")

    pick: Dict[str, Any] = {}
    for field, maximum in (("pickNumber", 500), ("roundNumber", 100), ("roundPick", 100)):
        number = _safe_positive_integer(value.get(field), field, maximum)
        if number is not None:
            pick[field] = number

    for field in _PICK_STRING_FIELDS:
        text = _safe_optional_string(value.get(field), field)
        if text is not None:
            pick[field] = text

    if "player" not in pick:
        raise LiveDraftValidationError("each pick must include a player")
    if "isUserPick" in value and not isinstance(value["isUserPick"], bool):
        raise LiveDraftValidationError("isUserPick must be a boolean")
    pick["isUserPick"] = (
        value.get("isUserPick") is True
        or pick.get("fantasyTeam", "").lower() == "your team"
    )
    return pick


def _repair_requested(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    repair = value.get("repair", False)
    if not isinstance(repair, bool):
        raise LiveDraftValidationError("repair must be a boolean")
    return repair


def sanitize_live_draft_context(value: Any) -> Dict[str, Any]:
    """Validate and whitelist the extension payload before writing it to disk."""

    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise LiveDraftValidationError("schemaVersion 1 is required")
    if value.get("source") != "yahoo-draft-recorder":
        raise LiveDraftValidationError("unsupported draft context source")

    draft = value.get("draft")
    picks_value = value.get("picks")
    if not isinstance(draft, dict) or not isinstance(picks_value, list):
        raise LiveDraftValidationError("draft and picks are required")
    if len(picks_value) > MAX_PICKS:
        raise LiveDraftValidationError(f"draft context cannot exceed {MAX_PICKS} picks")

    session_key = _safe_string(draft.get("sessionKey"), "sessionKey")
    if not _SESSION_KEY.fullmatch(session_key):
        raise LiveDraftValidationError("sessionKey has an invalid format")

    sport = _safe_string(draft.get("sport"), "sport", 32)
    league_id = _safe_string(draft.get("leagueId"), "leagueId", 64)
    if session_key != f"{sport}:{league_id}":
        raise LiveDraftValidationError("sessionKey must equal sport:leagueId")

    clean_draft: Dict[str, Any] = {
        "sport": sport,
        "leagueId": league_id,
        "teamId": _safe_string(draft.get("teamId"), "teamId", 64),
        "sessionKey": session_key,
    }
    updated_at = _safe_optional_string(draft.get("updatedAt"), "updatedAt")
    if updated_at:
        clean_draft["updatedAt"] = updated_at

    repair = _repair_requested(value)

    picks = [_sanitize_pick(pick) for pick in picks_value]
    picks.sort(key=lambda pick: pick.get("pickNumber", MAX_PICKS + 1))
    if repair:
        if not picks:
            raise LiveDraftValidationError("repair must include at least one pick")
        pick_numbers = [pick.get("pickNumber") for pick in picks]
        if any(pick_number is None for pick_number in pick_numbers):
            raise LiveDraftValidationError(
                "repair picks must all be positively numbered"
            )
        if len(set(pick_numbers)) != len(pick_numbers):
            raise LiveDraftValidationError("repair pick numbers must be unique")
        if pick_numbers != list(range(1, len(pick_numbers) + 1)):
            raise LiveDraftValidationError(
                "repair pick numbers must be contiguous from 1"
            )
    user_roster = [pick for pick in picks if pick["isUserPick"]]
    team_rosters: Dict[str, list[Dict[str, Any]]] = {}
    for pick in picks:
        team_rosters.setdefault(pick.get("fantasyTeam", "Unknown team"), []).append(pick)
    numbered_picks = [pick["pickNumber"] for pick in picks if "pickNumber" in pick]
    latest_pick = max(numbered_picks, default=0)

    generated_at = _safe_optional_string(value.get("generatedAt"), "generatedAt")
    return {
        "schemaVersion": 1,
        "source": "yahoo-draft-recorder",
        "generatedAt": generated_at,
        "draft": clean_draft,
        "summary": {
            "totalPicks": len(picks),
            "latestOverallPick": latest_pick,
            "nextOverallPick": latest_pick + 1,
            "userPickCount": len(user_roster),
        },
        "userRoster": user_roster,
        "teamRosters": team_rosters,
        "picks": picks,
    }


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_all(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveDraftValidationError(f"could not read live draft store: {error}") from error
    if not isinstance(value, dict):
        raise LiveDraftValidationError("live draft store is malformed")
    return value


def _prepare_store_directory(destination: Path, tighten_existing: bool) -> None:
    parent = destination.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
    except FileExistsError:
        if not parent.is_dir():
            raise
        created = False

    if created:
        parent.chmod(0o700)
    elif tighten_existing:
        if parent.is_symlink():
            raise LiveDraftValidationError(
                "default live draft store directory cannot be a symbolic link"
            )
        parent.chmod(0o700)


def save_live_draft(value: Any, path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Atomically save one sanitized draft session and return it."""

    repair = _repair_requested(value)
    context = sanitize_live_draft_context(value)
    custom_store_configured = path is not None or bool(
        os.getenv("FANTASY_FOOTBALL_LIVE_DRAFT_PATH")
    )
    destination = _store_path(path)
    with _STORE_LOCK:
        sessions = _read_all(destination)
        session_key = context["draft"]["sessionKey"]
        existing = sessions.get(session_key)
        if existing:
            existing_time = _timestamp(existing.get("generatedAt"))
            incoming_time = _timestamp(context.get("generatedAt"))
            existing_pick = existing.get("summary", {}).get("latestOverallPick", 0)
            incoming_pick = context.get("summary", {}).get("latestOverallPick", 0)
            if repair:
                existing_draft = existing.get("draft", {})
                if any(
                    context["draft"].get(field) != existing_draft.get(field)
                    for field in ("sport", "leagueId", "teamId", "sessionKey")
                ):
                    raise LiveDraftValidationError(
                        "repair draft identity does not match the saved session"
                    )
                if (
                    context.get("generatedAt") == existing.get("generatedAt")
                    and context == existing
                ):
                    _prepare_store_directory(
                        destination, tighten_existing=not custom_store_configured
                    )
                    return existing
                if (
                    existing_time is None
                    or incoming_time is None
                    or incoming_time <= existing_time
                ):
                    raise LiveDraftValidationError(
                        "repair snapshot must be newer than the saved session"
                    )
            elif incoming_pick < existing_pick or (
                existing_time and (incoming_time is None or incoming_time < existing_time)
            ):
                raise LiveDraftValidationError("stale live draft snapshot rejected")
        sessions[session_key] = context
        _prepare_store_directory(destination, tighten_existing=not custom_store_configured)
        handle, temporary_name = tempfile.mkstemp(
            prefix=".live-drafts-", suffix=".json", dir=destination.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temporary:
                json.dump(sessions, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    return context


def load_live_draft(
    league_id: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
    *,
    reject_ambiguous: bool = False,
) -> Optional[Dict[str, Any]]:
    """Load the newest context, optionally restricted to one Yahoo league ID."""

    with _STORE_LOCK:
        sessions = list(_read_all(_store_path(path)).values())
    if league_id is not None:
        sessions = [
            session
            for session in sessions
            if session.get("draft", {}).get("leagueId") == league_id
        ]
    if reject_ambiguous and len(sessions) > 1:
        session_keys = {
            session.get("draft", {}).get("sessionKey") for session in sessions
        }
        if len(session_keys) > 1:
            raise LiveDraftValidationError(
                "live draft league identity is ambiguous across stored sessions"
            )
    if not sessions:
        return None
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    return max(sessions, key=lambda session: _timestamp(session.get("generatedAt")) or minimum)
