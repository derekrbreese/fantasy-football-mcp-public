"""Cached, privacy-safe Sleeper NFL player experience enrichment."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Protocol

import aiohttp

DEFAULT_SLEEPER_PLAYER_CACHE_PATH = Path.home() / ".fantasy-football-mcp" / "sleeper-players.json"

_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
_CACHE_TTL_SECONDS = 86_400.0
_MAX_STALE_SECONDS = 3_888_000.0  # 45 days, matching Breakout Watch freshness.
_MAX_BODY_BYTES = 10_000_000
_MAX_PLAYERS = 10_000
_MAX_CACHE_BYTES = 2_000_000
_POSITIONS = frozenset({"RB", "WR", "TE"})
_POSITION_ALIASES = {"FB": "RB"}
_TEAM_ALIASES = {"JAC": "JAX", "WSH": "WAS"}
_PLAYER_FIELDS = frozenset({"sleeperId", "name", "position", "team", "yearsExperience", "yahooId"})


class SleeperPlayerProviderError(RuntimeError):
    """Sanitized provider/cache failure."""


class SleeperJsonTransport(Protocol):
    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> Mapping[str, Any]: ...


class AiohttpSleeperTransport:
    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> Mapping[str, Any]:
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(3.0, timeout_seconds),
            sock_read=timeout_seconds,
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(
                    url,
                    params=params,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise SleeperPlayerProviderError(
                            f"Sleeper returned HTTP status {response.status}"
                        )
                    content_length = response.content_length
                    if content_length is not None and content_length > max_body_bytes:
                        raise SleeperPlayerProviderError(
                            "Sleeper player catalog exceeded the body limit"
                        )
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16_384):
                        body.extend(chunk)
                        if len(body) > max_body_bytes:
                            raise SleeperPlayerProviderError(
                                "Sleeper player catalog exceeded the body limit"
                            )
        except asyncio.CancelledError:
            raise
        except SleeperPlayerProviderError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise SleeperPlayerProviderError("Sleeper player catalog request failed") from error

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SleeperPlayerProviderError(
                "Sleeper returned invalid player catalog JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise SleeperPlayerProviderError("Sleeper returned an invalid player catalog shape")
        return payload


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed) if parsed.tzinfo is not None else None


def _safe_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text or len(text) > maximum:
        return ""
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in text):
        return ""
    return text


def _name_key(value: Any) -> str:
    text = _safe_text(value, 120)
    ascii_name = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.casefold())


def _is_initialed_name(value: Any) -> bool:
    text = _safe_text(value, 120)
    first = re.split(r"[\s.-]+", text, maxsplit=1)[0]
    return len(re.sub(r"[^A-Za-z]", "", first)) <= 1


def _position(value: Any) -> str:
    position = _safe_text(value, 8).upper()
    return _POSITION_ALIASES.get(position, position)


def _team(value: Any) -> str:
    team = re.sub(r"[^A-Z0-9]", "", _safe_text(value, 8).upper())
    return _TEAM_ALIASES.get(team, team)


def _nonnegative_int(value: Any, maximum: int = 30) -> int | None:
    if isinstance(value, bool):
        return None
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isdigit():
        result = int(value)
    else:
        return None
    return result if 0 <= result <= maximum else None


def _positive_id(value: Any, maximum: int = 10_000_000_000) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip() if value is not None else ""
    if not text.isdigit() or text.startswith("0"):
        return None
    parsed = int(text)
    return text if parsed <= maximum else None


def _ranking_yahoo_id(value: Mapping[str, Any]) -> str | None:
    player_key = value.get("player_key") or value.get("playerKey")
    if not isinstance(player_key, str):
        return None
    match = re.fullmatch(r"[1-9]\d{0,9}\.p\.([1-9]\d{0,9})", player_key.strip())
    return match.group(1) if match else None


def _normalize_player(sleeper_id: Any, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    player_id = _positive_id(raw.get("player_id") or sleeper_id)
    name = _safe_text(raw.get("full_name"), 120)
    if not name:
        first = _safe_text(raw.get("first_name"), 60)
        last = _safe_text(raw.get("last_name"), 60)
        name = " ".join(part for part in (first, last) if part)
    position = _position(raw.get("position"))
    team = _team(raw.get("team"))
    years = _nonnegative_int(raw.get("years_exp"))
    if player_id is None or not name or position not in _POSITIONS or years is None:
        return None
    return {
        "sleeperId": player_id,
        "name": name,
        "position": position,
        "team": team,
        "yearsExperience": years,
        "yahooId": _positive_id(raw.get("yahoo_id")),
    }


class SleeperPlayerProvider:
    """Fetch, persist, and conservatively join Sleeper experience metadata."""

    def __init__(
        self,
        *,
        transport: SleeperJsonTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_path: str | Path | None = None,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = _CACHE_TTL_SECONDS,
        max_stale_seconds: float = _MAX_STALE_SECONDS,
        max_body_bytes: int = _MAX_BODY_BYTES,
        max_players: int = _MAX_PLAYERS,
    ) -> None:
        self._transport = transport or AiohttpSleeperTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache_path = Path(cache_path).expanduser() if cache_path is not None else None
        self._timeout_seconds = max(0.5, min(float(timeout_seconds), 10.0))
        self._cache_ttl_seconds = max(60.0, min(float(cache_ttl_seconds), 86_400.0))
        self._max_stale_seconds = max(
            self._cache_ttl_seconds,
            min(float(max_stale_seconds), _MAX_STALE_SECONDS),
        )
        self._max_body_bytes = max(65_536, min(int(max_body_bytes), _MAX_BODY_BYTES))
        self._max_players = max(1, min(int(max_players), _MAX_PLAYERS))
        self._lock = asyncio.Lock()
        self._memory_cache: tuple[datetime, tuple[dict[str, Any], ...]] | None = None

    @property
    def cache_path(self) -> Path:
        return self._cache_path or DEFAULT_SLEEPER_PLAYER_CACHE_PATH

    async def get_player_experience(self, players: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        now = _utc(self._clock())
        async with self._lock:
            catalog, fetched_at, stale, refresh_failed = await self._catalog(now)
        resolved = self._resolve(players, catalog)
        resolved_count = sum(item.get("identityResolved") is True for item in resolved)
        warnings = []
        if refresh_failed:
            warnings.append(
                "Sleeper player catalog refresh failed; using cached experience where available"
                if catalog
                else "Sleeper player experience is temporarily unavailable"
            )
        return {
            "status": "unavailable" if not catalog else "degraded" if warnings else "success",
            "provider": "Sleeper",
            "retrievedAt": _iso(now),
            "catalogFetchedAt": _iso(fetched_at) if fetched_at is not None else None,
            "cacheStale": stale,
            "refreshFailed": refresh_failed,
            "catalogPlayers": len(catalog),
            "requestedPlayers": len(players),
            "identityResolvedPlayers": resolved_count,
            "players": resolved,
            "warnings": warnings,
        }

    async def _catalog(
        self, now: datetime
    ) -> tuple[tuple[dict[str, Any], ...], datetime | None, bool, bool]:
        cached = self._memory_cache
        if cached is None:
            cached = self._read_cache()
            self._memory_cache = cached
        if cached is not None:
            age = (now - cached[0]).total_seconds()
            if 0 <= age < self._cache_ttl_seconds:
                return cached[1], cached[0], False, False
        else:
            age = math.inf

        try:
            payload = await self._transport.get_json(
                _PLAYERS_URL,
                params={"active": "true"},
                timeout_seconds=self._timeout_seconds,
                max_body_bytes=self._max_body_bytes,
            )
            if len(payload) > self._max_players:
                raise SleeperPlayerProviderError(
                    "Sleeper player catalog exceeded the record limit"
                )
            records = tuple(
                player
                for sleeper_id, raw in islice(payload.items(), self._max_players)
                if (player := _normalize_player(sleeper_id, raw)) is not None
            )
            if not records:
                raise SleeperPlayerProviderError(
                    "Sleeper returned no usable player experience records"
                )
            self._write_cache(now, records)
            self._memory_cache = (now, records)
            return records, now, False, False
        except asyncio.CancelledError:
            raise
        except Exception:
            if cached is not None and 0 <= age <= self._max_stale_seconds:
                return cached[1], cached[0], True, True
            return (), None, False, True

    def _read_cache(self) -> tuple[datetime, tuple[dict[str, Any], ...]] | None:
        path = self.cache_path
        try:
            if (
                not path.exists()
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > _MAX_CACHE_BYTES
            ):
                return None
            path.chmod(0o600)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or set(raw) != {
                "schemaVersion",
                "fetchedAt",
                "players",
            }:
                return None
            fetched_at = _parse_time(raw.get("fetchedAt"))
            raw_players = raw.get("players")
            if (
                raw.get("schemaVersion") != 1
                or fetched_at is None
                or not isinstance(raw_players, list)
                or len(raw_players) > self._max_players
            ):
                return None
            players: list[dict[str, Any]] = []
            for value in raw_players:
                if not isinstance(value, Mapping) or set(value) != _PLAYER_FIELDS:
                    return None
                normalized = _normalize_player(
                    value.get("sleeperId"),
                    {
                        "player_id": value.get("sleeperId"),
                        "full_name": value.get("name"),
                        "position": value.get("position"),
                        "team": value.get("team"),
                        "years_exp": value.get("yearsExperience"),
                        "yahoo_id": value.get("yahooId"),
                    },
                )
                if normalized is None or normalized != dict(value):
                    return None
                players.append(normalized)
            return fetched_at, tuple(players)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, fetched_at: datetime, players: Sequence[Mapping[str, Any]]) -> None:
        path = self.cache_path
        parent = path.parent
        try:
            if parent.is_symlink():
                raise SleeperPlayerProviderError("Sleeper player cache is unavailable")
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._cache_path is None:
                parent.chmod(0o700)
            if path.is_symlink():
                raise SleeperPlayerProviderError("Sleeper player cache is unavailable")
            payload = json.dumps(
                {
                    "schemaVersion": 1,
                    "fetchedAt": _iso(fetched_at),
                    "players": list(players),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            if len(payload.encode("utf-8")) > _MAX_CACHE_BYTES:
                raise SleeperPlayerProviderError("Sleeper player cache exceeded the size limit")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".sleeper-players-", suffix=".json", dir=parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                    temporary.write(payload)
                    temporary.write("\n")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, path)
                path.chmod(0o600)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        except SleeperPlayerProviderError:
            raise
        except OSError as error:
            raise SleeperPlayerProviderError("Sleeper player cache is unavailable") from error

    @staticmethod
    def _resolve(
        requested: Sequence[Mapping[str, Any]],
        catalog: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        by_yahoo: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        by_identity: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for player in catalog:
            position = _position(player.get("position"))
            yahoo_id = _positive_id(player.get("yahooId"))
            if yahoo_id is not None:
                by_yahoo.setdefault((yahoo_id, position), []).append(player)
            identity = (_name_key(player.get("name")), position, _team(player.get("team")))
            by_identity.setdefault(identity, []).append(player)

        result: list[dict[str, Any]] = []
        for value in requested:
            position = _position(value.get("position"))
            match: Mapping[str, Any] | None = None
            yahoo_id = _ranking_yahoo_id(value)
            if yahoo_id is not None:
                matches = by_yahoo.get((yahoo_id, position), [])
                if len(matches) == 1:
                    match = matches[0]
            if match is None and not _is_initialed_name(value.get("name")):
                identity = (
                    _name_key(value.get("name")),
                    position,
                    _team(value.get("team")),
                )
                matches = by_identity.get(identity, [])
                if len(matches) == 1 and all(identity):
                    match = matches[0]
            output = {
                "name": str(value.get("name") or ""),
                "position": str(value.get("position") or ""),
                "team": str(value.get("team") or ""),
                "identityResolved": match is not None,
                "experience_years": (match.get("yearsExperience") if match is not None else None),
                "experience_source": "Sleeper" if match is not None else None,
            }
            result.append(output)
        return result


__all__ = [
    "AiohttpSleeperTransport",
    "DEFAULT_SLEEPER_PLAYER_CACHE_PATH",
    "SleeperJsonTransport",
    "SleeperPlayerProvider",
    "SleeperPlayerProviderError",
]
