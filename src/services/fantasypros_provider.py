"""Bounded FantasyPros NFL injury and news enrichment.

The provider is deliberately isolated from the deterministic recommendation engine.
It fetches data in the service layer, keeps only a small allowlist, and uses exact
player identity matching before returning enrichment fields.

Official API reference: https://api.fantasypros.com/public/v2/docs/
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TypeVar

import aiohttp

from src.services.fantasypros_request_budget import (
    DEFAULT_DAILY_REQUEST_LIMIT,
    FantasyProsDailyRequestBudget,
    FantasyProsRequestBudgetExhausted,
    FantasyProsRequestBudgetUnavailable,
)
from src.services.fantasypros_snapshot_cache import (
    FantasyProsSnapshot,
    FantasyProsSnapshotCache,
    FantasyProsSnapshotCacheUnavailable,
    FantasyProsSnapshotKey,
)

_BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"
_PROVIDER = "FantasyPros"
_ENV_NAME = "FANTASY_PROS_API"
_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DST"})
_POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST"}
_NEWS_CATEGORIES = frozenset(
    {"injury", "breaking", "transaction", "rumor", "recap", "news", "commentary"}
)
_STATUS_ALIASES = {
    "active": "healthy",
    "healthy": "healthy",
    "probable": "probable",
    "questionable": "questionable",
    "doubtful": "doubtful",
    "out": "out",
    "ir": "ir",
    "cov-ir": "ir",
    "pup": "pup",
    "nfi": "nfi",
    "not starting": "not active",
    "not active": "not active",
    "suspended": "suspended",
    "day-to-day": "day-to-day",
}
_SHORT_STATUS_ALIASES = {
    "P": "probable",
    "Q": "questionable",
    "D": "doubtful",
    "O": "out",
    "OUT": "out",
    "IR": "ir",
    "COV-IR": "ir",
    "PUP": "pup",
    "S": "suspended",
    "NS": "not active",
}
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
# The public API permits one request per second and recommendation clients use a
# 30-second deadline. Two targeted identity lookups keep a cold, worst-case
# request bounded after the catalog, injury, and news snapshots.
_MAX_TARGETED_PLAYER_LOOKUPS = 2
_MAX_STALE_SNAPSHOT_AGE_SECONDS = 604_800.0


class FantasyProsProviderError(RuntimeError):
    """A sanitized provider failure that never includes credentials or response bodies."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class JsonHttpTransport(Protocol):
    """Minimal injectable HTTP boundary used by the provider."""

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> Mapping[str, Any]: ...


class AiohttpJsonTransport:
    """Default JSON transport with redirect, timeout, and body-size bounds."""

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int],
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
                    headers=headers,
                    params=params,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise FantasyProsProviderError(
                            f"FantasyPros returned HTTP status {response.status}",
                            status_code=response.status,
                        )
                    content_length = response.content_length
                    if content_length is not None and content_length > max_body_bytes:
                        raise FantasyProsProviderError("FantasyPros response exceeded the body limit")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16_384):
                        body.extend(chunk)
                        if len(body) > max_body_bytes:
                            raise FantasyProsProviderError(
                                "FantasyPros response exceeded the body limit"
                            )
        except asyncio.CancelledError:
            raise
        except FantasyProsProviderError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise FantasyProsProviderError("FantasyPros request failed") from exc

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FantasyProsProviderError("FantasyPros returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise FantasyProsProviderError("FantasyPros returned an invalid response shape")
        return payload


@dataclass(frozen=True)
class _CatalogPlayer:
    fantasypros_id: int
    name: str
    position: str
    team: str


@dataclass(frozen=True)
class _Injury:
    fantasypros_id: int
    name: str
    position: str
    team: str
    status: str
    updated_at: datetime | None


@dataclass(frozen=True)
class _News:
    fantasypros_id: int
    headline: str
    category: str
    published_at: datetime


_Record = TypeVar("_Record", _CatalogPlayer, _Injury, _News)


@dataclass(frozen=True)
class _CacheEntry:
    records: tuple[Any, ...]
    fetched_at: datetime
    truncated: bool
    returned_count: int
    reported_count: int | None
    reported_limit: int | None
    public_api_limited: bool


@dataclass(frozen=True)
class _LoadResult:
    records: tuple[Any, ...]
    truncated: bool = False
    failed: bool = False
    rate_limited: bool = False
    fetched_at: datetime | None = None
    returned_count: int = 0
    reported_count: int | None = None
    reported_limit: int | None = None
    public_api_limited: bool = False
    daily_budget_exhausted: bool = False
    daily_budget_unavailable: bool = False
    stale: bool = False
    refresh_failed: bool = False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_provider_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    return _utc(parsed) if parsed is not None else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = _URL_RE.sub("", text)
    text = "".join(character for character in text if character.isprintable())
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


def _position(value: Any) -> str:
    result = _safe_text(value, 8).upper()
    result = _POSITION_ALIASES.get(result, result)
    return result if result in _POSITIONS else ""


def _team(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _safe_text(value, 8).upper())[:5]


def _name_key(value: Any) -> str:
    safe = _safe_text(value, 120)
    ascii_name = unicodedata.normalize("NFKD", safe).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.casefold())


def _status(value: Any, short_value: Any) -> str:
    long_status = _safe_text(value, 32).casefold()
    if long_status in _STATUS_ALIASES:
        return _STATUS_ALIASES[long_status]
    short_status = _safe_text(short_value, 12).upper()
    return _SHORT_STATUS_ALIASES.get(short_status, "unknown")


def _news_category(value: Any) -> str:
    if isinstance(value, list):
        for item in value[:8]:
            normalized = _safe_text(item, 32).casefold()
            if normalized in _NEWS_CATEGORIES:
                return normalized
    normalized = _safe_text(value, 32).casefold()
    return normalized if normalized in _NEWS_CATEGORIES else "other"


def _fresh(source_time: datetime | None, now: datetime, max_age_seconds: float) -> bool:
    if source_time is None:
        return False
    age = (_utc(now) - _utc(source_time)).total_seconds()
    return -300.0 <= age <= max_age_seconds


class FantasyProsProvider:
    """Fetch and conservatively join FantasyPros NFL injury/news evidence."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        transport: JsonHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 5.0,
        max_body_bytes: int = 2_000_000,
        cache_ttl_seconds: float = 300.0,
        player_cache_ttl_seconds: float = 86_400.0,
        injury_max_age_seconds: float = 86_400.0,
        news_max_age_seconds: float = 172_800.0,
        max_players: int = 2_500,
        max_injuries: int = 1_000,
        news_limit: int = 100,
        recent_news_limit: int = 3,
        max_cache_entries: int = 256,
        request_interval_seconds: float = 1.05,
        failure_backoff_seconds: float = 60.0,
        rate_limit_backoff_seconds: float = 900.0,
        daily_request_limit: int = DEFAULT_DAILY_REQUEST_LIMIT,
        daily_budget_path: str | Path | None = None,
        snapshot_cache_path: str | Path | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        configured_key = api_key if api_key is not None else os.environ.get(_ENV_NAME)
        self._api_key = configured_key.strip() if isinstance(configured_key, str) else ""
        self._transport = transport or AiohttpJsonTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timeout_seconds = max(0.5, min(float(timeout_seconds), 10.0))
        self._max_body_bytes = max(65_536, min(int(max_body_bytes), 4_000_000))
        self._cache_ttl_seconds = max(1.0, min(float(cache_ttl_seconds), 3_600.0))
        self._player_cache_ttl_seconds = max(
            self._cache_ttl_seconds,
            min(float(player_cache_ttl_seconds), 86_400.0),
        )
        self._injury_max_age_seconds = max(0.0, min(float(injury_max_age_seconds), 2_592_000.0))
        self._news_max_age_seconds = max(0.0, min(float(news_max_age_seconds), 604_800.0))
        self._max_players = max(1, min(int(max_players), 5_000))
        self._max_injuries = max(1, min(int(max_injuries), 2_000))
        self._news_limit = max(1, min(int(news_limit), 100))
        self._recent_news_limit = max(1, min(int(recent_news_limit), 5))
        self._max_cache_entries = max(16, min(int(max_cache_entries), 1_024))
        self._request_interval_seconds = max(
            0.0, min(float(request_interval_seconds), 5.0)
        )
        self._failure_backoff_seconds = max(
            1.0, min(float(failure_backoff_seconds), 3_600.0)
        )
        self._rate_limit_backoff_seconds = max(
            self._failure_backoff_seconds,
            min(float(rate_limit_backoff_seconds), 86_400.0),
        )
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._daily_request_budget = FantasyProsDailyRequestBudget(
            path=daily_budget_path,
            daily_limit=daily_request_limit,
        )
        self._snapshot_cache = FantasyProsSnapshotCache(path=snapshot_cache_path)
        self._snapshot_cache_available = True
        self._cache: OrderedDict[
            tuple[str, tuple[tuple[str, str | int], ...]], _CacheEntry
        ] = OrderedDict()
        self._failure_backoff: OrderedDict[
            tuple[str, tuple[tuple[str, str | int], ...]], tuple[float, bool]
        ] = OrderedDict()
        self._request_lock = asyncio.Lock()
        self._request_gate = asyncio.Lock()
        self._next_request_at = 0.0
        self._daily_budget_exhausted_until: datetime | None = None
        self._daily_budget_unavailable_until = 0.0
        self._provider_rate_limited_until = 0.0

    def __repr__(self) -> str:
        return f"{type(self).__name__}(configured={bool(self._api_key)})"

    def _now(self) -> datetime:
        return _utc(self._clock())

    async def get_player_updates(
        self,
        players: Sequence[Mapping[str, Any]],
        *,
        year: int | None = None,
        week: int | None = 0,
    ) -> dict[str, Any]:
        """Return one allowlisted enrichment record per input player, in input order."""

        now = self._now()
        identities = [self._input_identity(player) for player in players]
        if not self._api_key:
            return {
                "status": "unavailable",
                "provider": _PROVIDER,
                "retrievedAt": _iso(now),
                "players": [self._unknown_player(identity, now) for identity in identities],
                "warnings": [
                    f"FantasyPros enrichment is unavailable because {_ENV_NAME} is not configured"
                ],
            }

        request_year = self._bounded_year(year, now.year)
        request_week = max(0, min(int(week or 0), 25))
        async with self._request_lock:
            catalog_result, injury_result, news_result = await asyncio.gather(
                self._safe_load(
                    "players",
                    {"ecr": "included"},
                    "players",
                    self._max_players,
                    self._player_cache_ttl_seconds,
                    self._catalog_player,
                ),
                self._safe_load(
                    "injuries",
                    {
                        "year": request_year,
                        "week": request_week,
                        "include_probabilities": "true",
                    },
                    "injuries",
                    self._max_injuries,
                    self._cache_ttl_seconds,
                    self._injury,
                ),
                self._safe_load(
                    "news",
                    {"limit": self._news_limit, "order_by": "updated"},
                    "items",
                    self._news_limit,
                    self._cache_ttl_seconds,
                    self._news,
                ),
            )

            catalog = [
                record for record in catalog_result.records if isinstance(record, _CatalogPlayer)
            ]
            injuries = [record for record in injury_result.records if isinstance(record, _Injury)]
            news = [record for record in news_result.records if isinstance(record, _News)]
            injury_players = self._injury_players(injuries)
            known_ids = {
                player.fantasypros_id for player in (*catalog, *injury_players)
            }
            unresolved_news_ids: list[int] = []
            for item in sorted(news, key=lambda value: value.published_at, reverse=True):
                if news_result.stale:
                    break
                if not _fresh(item.published_at, now, self._news_max_age_seconds):
                    continue
                if (
                    item.fantasypros_id not in known_ids
                    and item.fantasypros_id not in unresolved_news_ids
                ):
                    unresolved_news_ids.append(item.fantasypros_id)
            targeted_capped = len(unresolved_news_ids) > _MAX_TARGETED_PLAYER_LOOKUPS
            targeted_ids = unresolved_news_ids[:_MAX_TARGETED_PLAYER_LOOKUPS]
            targeted_results = await asyncio.gather(
                *(
                    self._safe_load(
                        "players",
                        {"player": fantasypros_id},
                        "players",
                        1,
                        self._player_cache_ttl_seconds,
                        self._catalog_player,
                    )
                    for fantasypros_id in targeted_ids
                )
            )

        warnings = self._warnings(catalog_result, injury_result, news_result)
        targeted_players: list[_CatalogPlayer] = []
        targeted_failed = False
        targeted_incomplete = False
        for fantasypros_id, load_result in zip(targeted_ids, targeted_results, strict=True):
            matches = [
                record
                for record in load_result.records
                if isinstance(record, _CatalogPlayer)
                and record.fantasypros_id == fantasypros_id
            ]
            if len(matches) == 1:
                targeted_players.append(matches[0])
                if load_result.refresh_failed:
                    targeted_failed = True
            elif (
                load_result.daily_budget_exhausted
                or load_result.daily_budget_unavailable
            ):
                continue
            elif load_result.failed:
                targeted_failed = True
            else:
                targeted_incomplete = True
        targeted_budget_warning = self._daily_budget_warning(targeted_results)
        if targeted_budget_warning is not None and targeted_budget_warning not in warnings:
            warnings.append(targeted_budget_warning)
        if targeted_capped:
            warnings.append(
                "FantasyPros recent-news identity coverage exceeded the bounded lookup limit"
            )
        if targeted_failed:
            warnings.append("FantasyPros recent-news player identity is temporarily unavailable")
        elif targeted_incomplete:
            warnings.append("FantasyPros recent-news player identity coverage is incomplete")

        combined_catalog = list(dict.fromkeys((*catalog, *injury_players, *targeted_players)))
        resolved = self._resolve_identities(identities, combined_catalog)
        injuries_by_id = self._latest_injuries(injuries)
        news_by_id = self._news_by_id(news)
        enriched = [
            self._enrichment(
                identity,
                player,
                injuries_by_id,
                news_by_id,
                now,
                injury_snapshot_at=injury_result.fetched_at,
                injury_snapshot_stale=injury_result.stale,
                news_snapshot_stale=news_result.stale,
            )
            for identity, player in zip(identities, resolved, strict=True)
        ]
        return {
            "status": "degraded" if warnings else "success",
            "provider": _PROVIDER,
            "retrievedAt": _iso(now),
            "players": enriched,
            "warnings": warnings,
            "coverage": {
                "playerCatalog": self._coverage(catalog_result),
                "injuries": self._coverage(injury_result),
                "news": self._coverage(news_result),
                "targetedPlayerLookups": {
                    "attempted": len(targeted_ids),
                    "resolved": len(targeted_players),
                    "capped": targeted_capped,
                },
            },
        }

    @staticmethod
    def _bounded_year(value: int | None, current_year: int) -> int:
        try:
            requested = int(value) if value is not None else current_year
        except (TypeError, ValueError):
            requested = current_year
        return max(2012, min(requested, current_year + 1))

    async def _safe_load(
        self,
        endpoint: str,
        params: dict[str, str | int],
        array_key: str,
        record_limit: int,
        ttl_seconds: float,
        normalizer: Callable[[Mapping[str, Any]], _Record | None],
    ) -> _LoadResult:
        try:
            return await self._load(
                endpoint,
                params,
                array_key,
                record_limit,
                ttl_seconds,
                normalizer,
            )
        except asyncio.CancelledError:
            raise
        except FantasyProsRequestBudgetExhausted:
            return _LoadResult(
                (),
                failed=True,
                daily_budget_exhausted=True,
                refresh_failed=True,
            )
        except FantasyProsRequestBudgetUnavailable:
            return _LoadResult(
                (),
                failed=True,
                daily_budget_unavailable=True,
                refresh_failed=True,
            )
        except FantasyProsProviderError as error:
            rate_limited = error.status_code == 429
            self._remember_failure(
                (endpoint, tuple(sorted(params.items()))),
                seconds=(
                    self._rate_limit_backoff_seconds
                    if rate_limited
                    else self._failure_backoff_seconds
                ),
                rate_limited=rate_limited,
            )
            return _LoadResult(
                (),
                failed=True,
                rate_limited=rate_limited,
                refresh_failed=True,
            )
        except Exception:
            self._remember_failure((endpoint, tuple(sorted(params.items()))))
            return _LoadResult((), failed=True, refresh_failed=True)

    def _remember_failure(
        self,
        cache_key: tuple[str, tuple[tuple[str, str | int], ...]],
        *,
        seconds: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        self._failure_backoff[cache_key] = (
            self._monotonic()
            + (self._failure_backoff_seconds if seconds is None else seconds),
            rate_limited,
        )
        self._failure_backoff.move_to_end(cache_key)
        while len(self._failure_backoff) > self._max_cache_entries:
            self._failure_backoff.popitem(last=False)

    async def _paced_get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int],
    ) -> Mapping[str, Any]:
        async with self._request_gate:
            if self._monotonic() < self._provider_rate_limited_until:
                raise FantasyProsProviderError(
                    "FantasyPros is rate-limited",
                    status_code=429,
                )
            self._reserve_daily_request()
            delay = self._next_request_at - self._monotonic()
            if delay > 0.0:
                await self._sleep(delay)
            started_at = self._monotonic()
            self._next_request_at = started_at + self._request_interval_seconds
            try:
                return await self._transport.get_json(
                    url,
                    headers=headers,
                    params=params,
                    timeout_seconds=self._timeout_seconds,
                    max_body_bytes=self._max_body_bytes,
                )
            except FantasyProsProviderError as error:
                if error.status_code == 429:
                    observed_at = self._monotonic()
                    if observed_at >= self._provider_rate_limited_until:
                        self._provider_rate_limited_until = (
                            observed_at + self._rate_limit_backoff_seconds
                        )
                raise

    def _reserve_daily_request(self) -> None:
        now = self._now()
        exhausted_until = self._daily_budget_exhausted_until
        if exhausted_until is not None:
            if now < exhausted_until:
                raise FantasyProsRequestBudgetExhausted(exhausted_until)
            self._daily_budget_exhausted_until = None

        monotonic_now = self._monotonic()
        if monotonic_now < self._daily_budget_unavailable_until:
            raise FantasyProsRequestBudgetUnavailable

        try:
            self._daily_request_budget.reserve(now)
        except FantasyProsRequestBudgetExhausted as error:
            self._daily_budget_exhausted_until = error.retry_at
            raise
        except FantasyProsRequestBudgetUnavailable:
            if monotonic_now >= self._daily_budget_unavailable_until:
                self._daily_budget_unavailable_until = (
                    monotonic_now + self._failure_backoff_seconds
                )
            raise
        else:
            self._daily_budget_unavailable_until = 0.0

    async def _load(
        self,
        endpoint: str,
        params: dict[str, str | int],
        array_key: str,
        record_limit: int,
        ttl_seconds: float,
        normalizer: Callable[[Mapping[str, Any]], _Record | None],
    ) -> _LoadResult:
        cache_key = (endpoint, tuple(sorted(params.items())))
        now = self._now()
        cached = self._cache.get(cache_key)
        try:
            snapshot_key = self._snapshot_key(endpoint, params, record_limit)
        except ValueError:
            snapshot_key = None
        if cached is None and snapshot_key is not None:
            cached = self._load_persistent_snapshot(snapshot_key)
            if cached is not None:
                self._remember_cache_entry(cache_key, cached)

        stale: _CacheEntry | None = None
        if cached is not None:
            cache_age = (now - cached.fetched_at).total_seconds()
            effective_ttl = self._effective_ttl(endpoint, params, cached, ttl_seconds)
            if 0.0 <= cache_age < effective_ttl:
                self._cache.move_to_end(cache_key)
                return self._load_result(cached)
            if 0.0 <= cache_age <= _MAX_STALE_SNAPSHOT_AGE_SECONDS:
                stale = cached
            else:
                self._cache.pop(cache_key, None)

        failed_backoff = self._failure_backoff.get(cache_key)
        if failed_backoff is not None:
            failed_until, rate_limited = failed_backoff
            if failed_until > self._monotonic():
                self._failure_backoff.move_to_end(cache_key)
                return self._failed_load_result(stale, rate_limited=rate_limited)
            self._failure_backoff.pop(cache_key, None)

        try:
            payload = await self._paced_get_json(
                f"{_BASE_URL}/{endpoint}",
                headers={"x-api-key": self._api_key},
                params=params,
            )
            raw_records = payload.get(array_key)
            if not isinstance(raw_records, list):
                raise FantasyProsProviderError("FantasyPros returned an invalid response shape")
            if payload.get("sport") not in (None, "NFL"):
                raise FantasyProsProviderError("FantasyPros returned an invalid sport")
            truncated = len(raw_records) > record_limit
            normalized: list[_Record] = []
            for raw in raw_records[:record_limit]:
                if not isinstance(raw, Mapping):
                    continue
                item = normalizer(raw)
                if item is not None:
                    normalized.append(item)
            entry = _CacheEntry(
                tuple(normalized),
                now,
                truncated,
                len(raw_records),
                _nonnegative_int(payload.get("count")),
                _positive_int(payload.get("limit")),
                payload.get("public_api_limited") is True,
            )
        except asyncio.CancelledError:
            raise
        except FantasyProsRequestBudgetExhausted:
            return self._failed_load_result(stale, daily_budget_exhausted=True)
        except FantasyProsRequestBudgetUnavailable:
            return self._failed_load_result(stale, daily_budget_unavailable=True)
        except FantasyProsProviderError as error:
            rate_limited = error.status_code == 429
            self._remember_failure(
                cache_key,
                seconds=(
                    self._rate_limit_backoff_seconds
                    if rate_limited
                    else self._failure_backoff_seconds
                ),
                rate_limited=rate_limited,
            )
            return self._failed_load_result(stale, rate_limited=rate_limited)
        except Exception:
            self._remember_failure(cache_key)
            return self._failed_load_result(stale)

        self._remember_cache_entry(cache_key, entry)
        self._failure_backoff.pop(cache_key, None)
        if snapshot_key is not None:
            self._save_persistent_snapshot(snapshot_key, entry)
        return self._load_result(entry)

    @staticmethod
    def _effective_ttl(
        endpoint: str,
        params: Mapping[str, str | int],
        entry: _CacheEntry,
        ttl_seconds: float,
    ) -> float:
        is_partial_catalog = (
            endpoint == "players"
            and params == {"ecr": "included"}
            and entry.reported_count is not None
            and entry.reported_count > entry.returned_count
        )
        return min(ttl_seconds, 300.0) if is_partial_catalog else ttl_seconds

    def _remember_cache_entry(
        self,
        cache_key: tuple[str, tuple[tuple[str, str | int], ...]],
        entry: _CacheEntry,
    ) -> None:
        self._cache[cache_key] = entry
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_cache_entries:
            self._cache.popitem(last=False)

    @staticmethod
    def _load_result(entry: _CacheEntry) -> _LoadResult:
        return _LoadResult(
            entry.records,
            truncated=entry.truncated,
            fetched_at=entry.fetched_at,
            returned_count=entry.returned_count,
            reported_count=entry.reported_count,
            reported_limit=entry.reported_limit,
            public_api_limited=entry.public_api_limited,
        )

    @staticmethod
    def _failed_load_result(
        stale: _CacheEntry | None,
        *,
        rate_limited: bool = False,
        daily_budget_exhausted: bool = False,
        daily_budget_unavailable: bool = False,
    ) -> _LoadResult:
        if stale is None:
            return _LoadResult(
                (),
                failed=True,
                rate_limited=rate_limited,
                daily_budget_exhausted=daily_budget_exhausted,
                daily_budget_unavailable=daily_budget_unavailable,
                refresh_failed=True,
            )
        return _LoadResult(
            stale.records,
            truncated=stale.truncated,
            failed=True,
            rate_limited=rate_limited,
            fetched_at=stale.fetched_at,
            returned_count=stale.returned_count,
            reported_count=stale.reported_count,
            reported_limit=stale.reported_limit,
            public_api_limited=stale.public_api_limited,
            daily_budget_exhausted=daily_budget_exhausted,
            daily_budget_unavailable=daily_budget_unavailable,
            stale=True,
            refresh_failed=True,
        )

    @staticmethod
    def _snapshot_key(
        endpoint: str,
        params: Mapping[str, str | int],
        record_limit: int,
    ) -> FantasyProsSnapshotKey | None:
        if endpoint == "players" and params == {"ecr": "included"}:
            return FantasyProsSnapshotKey("players", "catalog", record_limit=record_limit)
        if endpoint == "injuries" and set(params) == {
            "year",
            "week",
            "include_probabilities",
        }:
            if params.get("include_probabilities") != "true":
                return None
            year = params.get("year")
            week = params.get("week")
            if type(year) is not int or type(week) is not int:
                return None
            return FantasyProsSnapshotKey(
                "injuries",
                "weekly",
                season=year,
                week=week,
                record_limit=record_limit,
            )
        if endpoint == "news" and set(params) == {"limit", "order_by"}:
            if params.get("order_by") != "updated":
                return None
            request_limit = params.get("limit")
            if type(request_limit) is not int:
                return None
            return FantasyProsSnapshotKey(
                "news",
                "recent",
                request_limit=request_limit,
                record_limit=record_limit,
            )
        return None

    def _load_persistent_snapshot(
        self,
        key: FantasyProsSnapshotKey,
    ) -> _CacheEntry | None:
        if not self._snapshot_cache_available:
            return None
        try:
            snapshot = self._snapshot_cache.load(key)
        except FantasyProsSnapshotCacheUnavailable:
            self._snapshot_cache_available = False
            return None
        if snapshot is None:
            return None
        try:
            records = self._records_from_snapshot(key.endpoint, snapshot.records)
        except (TypeError, ValueError):
            self._snapshot_cache_available = False
            return None
        return _CacheEntry(
            records,
            snapshot.fetched_at,
            snapshot.truncated,
            snapshot.returned_count,
            snapshot.reported_count,
            snapshot.reported_limit,
            snapshot.public_api_limited,
        )

    def _save_persistent_snapshot(
        self,
        key: FantasyProsSnapshotKey,
        entry: _CacheEntry,
    ) -> None:
        if not self._snapshot_cache_available:
            return
        snapshot = FantasyProsSnapshot(
            records=self._records_for_snapshot(key.endpoint, entry.records),
            fetched_at=entry.fetched_at,
            truncated=entry.truncated,
            returned_count=entry.returned_count,
            reported_count=entry.reported_count,
            reported_limit=entry.reported_limit,
            public_api_limited=entry.public_api_limited,
        )
        try:
            self._snapshot_cache.save(key, snapshot)
        except FantasyProsSnapshotCacheUnavailable:
            self._snapshot_cache_available = False

    @staticmethod
    def _records_for_snapshot(
        endpoint: str,
        records: Sequence[Any],
    ) -> tuple[dict[str, Any], ...]:
        if endpoint == "players":
            return tuple(
                {
                    "id": record.fantasypros_id,
                    "name": record.name,
                    "position": record.position,
                    "team": record.team,
                }
                for record in records
                if isinstance(record, _CatalogPlayer)
            )
        if endpoint == "injuries":
            return tuple(
                {
                    "id": record.fantasypros_id,
                    "name": record.name,
                    "position": record.position,
                    "team": record.team,
                    "status": record.status,
                    "updatedAt": _iso(record.updated_at),
                }
                for record in records
                if isinstance(record, _Injury)
            )
        if endpoint == "news":
            return tuple(
                {
                    "id": record.fantasypros_id,
                    "headline": record.headline,
                    "category": record.category,
                    "publishedAt": _iso(record.published_at),
                }
                for record in records
                if isinstance(record, _News)
            )
        return ()

    @staticmethod
    def _records_from_snapshot(
        endpoint: str,
        records: Sequence[Mapping[str, Any]],
    ) -> tuple[Any, ...]:
        if endpoint == "players":
            return tuple(
                _CatalogPlayer(
                    int(record["id"]),
                    str(record["name"]),
                    str(record["position"]),
                    str(record["team"]),
                )
                for record in records
            )
        if endpoint == "injuries":
            return tuple(
                _Injury(
                    int(record["id"]),
                    str(record["name"]),
                    str(record["position"]),
                    str(record["team"]),
                    str(record["status"]),
                    _parse_provider_time(record["updatedAt"]),
                )
                for record in records
            )
        if endpoint == "news":
            result: list[_News] = []
            for record in records:
                published_at = _parse_provider_time(record["publishedAt"])
                if published_at is None:
                    raise ValueError("invalid cached timestamp")
                result.append(
                    _News(
                        int(record["id"]),
                        str(record["headline"]),
                        str(record["category"]),
                        published_at,
                    )
                )
            return tuple(result)
        raise ValueError("invalid cached endpoint")

    @staticmethod
    def _catalog_player(raw: Mapping[str, Any]) -> _CatalogPlayer | None:
        fantasypros_id = _positive_int(raw.get("player_id"))
        name = _safe_text(raw.get("player_name"), 120)
        position = _position(raw.get("position_id"))
        team = _team(raw.get("team_id"))
        if fantasypros_id is None or not name or not position or not team:
            return None
        return _CatalogPlayer(fantasypros_id, name, position, team)

    @staticmethod
    def _injury(raw: Mapping[str, Any]) -> _Injury | None:
        fantasypros_id = _positive_int(raw.get("player_id"))
        if fantasypros_id is None:
            return None
        return _Injury(
            fantasypros_id,
            _safe_text(raw.get("name"), 120),
            _position(raw.get("position_id")),
            _team(raw.get("team_id")),
            _status(raw.get("status"), raw.get("status_short")),
            _parse_provider_time(raw.get("injury_update_date")),
        )

    @staticmethod
    def _news(raw: Mapping[str, Any]) -> _News | None:
        fantasypros_id = _positive_int(raw.get("player_id"))
        headline = _safe_text(raw.get("title"), 240)
        published_at = _parse_provider_time(raw.get("updated") or raw.get("created"))
        if fantasypros_id is None or not headline or published_at is None:
            return None
        return _News(
            fantasypros_id,
            headline,
            _news_category(raw.get("categories") or raw.get("category")),
            published_at,
        )

    @staticmethod
    def _input_identity(raw: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": _safe_text(raw.get("name"), 120),
            "position": _position(raw.get("position")),
            "team": _team(raw.get("team") or raw.get("nflTeam")),
            "fantasypros_id": _positive_int(
                raw.get("fantasypros_id") or raw.get("fantasyProsId")
            ),
        }

    @staticmethod
    def _resolve_identities(
        identities: Sequence[Mapping[str, Any]], catalog: Sequence[_CatalogPlayer]
    ) -> list[_CatalogPlayer | None]:
        by_id = {player.fantasypros_id: player for player in catalog}
        by_identity: dict[tuple[str, str, str], list[_CatalogPlayer]] = defaultdict(list)
        dst_by_team: dict[str, list[_CatalogPlayer]] = defaultdict(list)
        for player in catalog:
            by_identity[(_name_key(player.name), player.position, player.team)].append(player)
            if player.position == "DST":
                dst_by_team[player.team].append(player)

        result: list[_CatalogPlayer | None] = []
        for identity in identities:
            fantasypros_id = _positive_int(identity.get("fantasypros_id"))
            if fantasypros_id is not None:
                catalog_player = by_id.get(fantasypros_id)
                result.append(
                    catalog_player
                    or _CatalogPlayer(
                        fantasypros_id,
                        str(identity.get("name") or ""),
                        str(identity.get("position") or ""),
                        str(identity.get("team") or ""),
                    )
                )
                continue
            name = _name_key(identity.get("name"))
            position = str(identity.get("position") or "")
            team = str(identity.get("team") or "")
            matches = by_identity.get((name, position, team), []) if name else []
            if not matches and position == "DST" and team:
                matches = dst_by_team.get(team, [])
            unique_matches = {
                match.fantasypros_id: match for match in matches
            }
            result.append(next(iter(unique_matches.values())) if len(unique_matches) == 1 else None)
        return result

    @staticmethod
    def _latest_injuries(injuries: Sequence[_Injury]) -> dict[int, _Injury]:
        result: dict[int, _Injury] = {}
        for injury in injuries:
            current = result.get(injury.fantasypros_id)
            if current is None or (
                injury.updated_at is not None
                and (current.updated_at is None or injury.updated_at > current.updated_at)
            ):
                result[injury.fantasypros_id] = injury
        return result

    @staticmethod
    def _injury_players(injuries: Sequence[_Injury]) -> list[_CatalogPlayer]:
        return [
            _CatalogPlayer(injury.fantasypros_id, injury.name, injury.position, injury.team)
            for injury in injuries
            if injury.name and injury.position and injury.team
        ]

    @staticmethod
    def _news_by_id(news: Sequence[_News]) -> dict[int, list[_News]]:
        result: dict[int, list[_News]] = defaultdict(list)
        for item in news:
            result[item.fantasypros_id].append(item)
        for items in result.values():
            items.sort(key=lambda item: item.published_at, reverse=True)
        return dict(result)

    def _enrichment(
        self,
        identity: Mapping[str, Any],
        player: _CatalogPlayer | None,
        injuries: Mapping[int, _Injury],
        news: Mapping[int, Sequence[_News]],
        now: datetime,
        *,
        injury_snapshot_at: datetime | None,
        injury_snapshot_stale: bool,
        news_snapshot_stale: bool,
    ) -> dict[str, Any]:
        if player is None:
            return self._unknown_player(identity, now)
        result = self._unknown_player(
            {
                "name": player.name or identity.get("name"),
                "position": player.position or identity.get("position"),
                "team": player.team or identity.get("team"),
                "fantasypros_id": player.fantasypros_id,
            },
            now,
            identity_resolved=True,
        )
        injury = injuries.get(player.fantasypros_id)
        if injury is not None:
            result["injury_updated_at"] = _iso(injury.updated_at)
            result["injury_snapshot_at"] = _iso(injury_snapshot_at)
            known_status = injury.status != "unknown"
            is_fresh = (
                known_status
                and not injury_snapshot_stale
                and _fresh(injury_snapshot_at, now, self._injury_max_age_seconds)
            )
            result["injury_fresh"] = is_fresh
            if is_fresh:
                result["injury_status"] = injury.status
                result["injury_source"] = _PROVIDER

        player_news = list(news.get(player.fantasypros_id, ()))
        if player_news:
            result["news_updated_at"] = _iso(player_news[0].published_at)
        fresh_news = []
        if not news_snapshot_stale:
            fresh_news = [
                item
                for item in player_news
                if _fresh(item.published_at, now, self._news_max_age_seconds)
            ][: self._recent_news_limit]
        if fresh_news:
            result["news_source"] = _PROVIDER
            result["news_fresh"] = True
            result["recentNews"] = [
                {
                    "headline": item.headline,
                    "category": item.category,
                    "publishedAt": _iso(item.published_at),
                }
                for item in fresh_news
            ]
        return result

    @staticmethod
    def _unknown_player(
        identity: Mapping[str, Any],
        now: datetime,
        *,
        identity_resolved: bool = False,
    ) -> dict[str, Any]:
        return {
            "name": str(identity.get("name") or ""),
            "position": str(identity.get("position") or ""),
            "team": str(identity.get("team") or ""),
            "fantasypros_id": _positive_int(identity.get("fantasypros_id")),
            "identityResolved": identity_resolved,
            "injury_status": "unknown",
            "injury_source": None,
            "injury_updated_at": None,
            "injury_snapshot_at": None,
            "injury_fresh": False,
            "news_source": None,
            "news_updated_at": None,
            "news_fresh": False,
            "recentNews": [],
            "retrievedAt": _iso(now),
        }

    @staticmethod
    def _warnings(
        catalog: _LoadResult,
        injuries: _LoadResult,
        news: _LoadResult,
    ) -> list[str]:
        results = (
            ("player catalog", catalog),
            ("injuries", injuries),
            ("news", news),
        )
        warnings: list[str] = []
        budget_warning = FantasyProsProvider._daily_budget_warning(
            tuple(result for _label, result in results)
        )
        if budget_warning is not None:
            warnings.append(budget_warning)
        for label, result in results:
            if result.daily_budget_exhausted or result.daily_budget_unavailable:
                continue
            if result.rate_limited:
                if result.stale:
                    warnings.append(
                        f"FantasyPros {label} refresh is rate-limited; using a stale "
                        "snapshot with unknown freshness"
                    )
                else:
                    warnings.append(
                        f"FantasyPros {label} is rate-limited; missing data remains unknown"
                    )
            elif result.failed:
                if result.stale:
                    warnings.append(
                        f"FantasyPros {label} refresh failed; using a stale snapshot "
                        "with unknown freshness"
                    )
                else:
                    warnings.append(f"FantasyPros {label} is temporarily unavailable")
            elif result.truncated:
                warnings.append(f"FantasyPros {label} exceeded the bounded record limit")
            elif result.public_api_limited or (
                result.reported_count is not None
                and result.reported_count > result.returned_count
            ):
                warnings.append(f"FantasyPros {label} coverage is limited by the public API")
        return warnings

    @staticmethod
    def _daily_budget_warning(results: Sequence[_LoadResult]) -> str | None:
        if any(result.daily_budget_exhausted for result in results):
            if any(result.daily_budget_exhausted and result.stale for result in results):
                return (
                    "FantasyPros daily request budget is exhausted; using stale snapshots "
                    "where available and missing data remains unknown until the next UTC day"
                )
            return (
                "FantasyPros daily request budget is exhausted; missing data remains "
                "unknown until the next UTC day"
            )
        if any(result.daily_budget_unavailable for result in results):
            if any(result.daily_budget_unavailable and result.stale for result in results):
                return (
                    "FantasyPros daily request budget is unavailable; using stale snapshots "
                    "where available and missing data remains unknown"
                )
            return "FantasyPros daily request budget is unavailable; missing data remains unknown"
        return None

    @staticmethod
    def _coverage(result: _LoadResult) -> dict[str, Any]:
        return {
            "fetchedAt": _iso(result.fetched_at),
            "returned": result.returned_count,
            "reportedCount": result.reported_count,
            "reportedLimit": result.reported_limit,
            "publicApiLimited": result.public_api_limited,
            "stale": result.stale,
            "refreshFailed": result.refresh_failed,
        }


__all__ = [
    "AiohttpJsonTransport",
    "FantasyProsProvider",
    "FantasyProsProviderError",
    "JsonHttpTransport",
]
