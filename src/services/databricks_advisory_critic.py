"""Optional, advisory-only Databricks critic for deterministic draft results.

This module is deliberately isolated from the deterministic recommendation engine.
It sends only a small allowlisted numeric/positional summary, never raw draft state,
identities, URLs, news, credentials, or arbitrary browser fields. Results are held
only in a bounded in-memory cache and can never replace or reorder recommendations.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import importlib
import json
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

_PROVIDER = "Databricks"
_SCHEMA_VERSION = 1
_MAX_CANDIDATES = 5
_MAX_RECENT_POSITIONS = 8
_MAX_RAW_RESPONSE_CHARS = 8_192
_MAX_SUMMARY_CHARS = 240
_MAX_CAUTION_CHARS = 240
_MAX_CAUTIONS = 2
_MAX_MODEL_CHARS = 128
_MAX_OUTPUT_TOKENS = 256
_MAX_INFLIGHT_REQUESTS = 4
_MAX_ACTIVE_SDK_CALLS = 2
_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DST"})
_POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST"}
_RISK_STATUSES = frozenset(
    {
        "healthy",
        "probable",
        "questionable",
        "doubtful",
        "out",
        "ir",
        "pup",
        "nfi",
        "not active",
        "suspended",
        "day-to-day",
        "unknown",
    }
)
_QUALITY_FLAGS = frozenset(
    {
        "external_news_unavailable",
        "inferred_team_count",
        "injury_status_unavailable",
        "roster_slots_unavailable",
        "state_stale",
        "unresolved_drafted_players",
    }
)
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SENSITIVE_VALUE_RE = re.compile(
    r"\b(?:api[_-]?key|authorization|password|secret|token)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")

_ENV_ENABLED = "FANTASY_FOOTBALL_DATABRICKS_ADVISORY_ENABLED"
_ENV_HOST = "FANTASY_FOOTBALL_DATABRICKS_HOST"
_ENV_MODEL = "FANTASY_FOOTBALL_DATABRICKS_MODEL"
_ENV_TIMEOUT = "FANTASY_FOOTBALL_DATABRICKS_ADVISORY_TIMEOUT_SECONDS"

_PROMPT_PREFIX = (
    "Review this immutable deterministic fantasy draft order using only the sanitized JSON. "
    "Do not select, reorder, rename, or invent candidates or facts. Return only schema-valid "
    "JSON with a summary of at most 240 characters and at most two concise cautions. Treat all "
    "probabilities as uncalibrated heuristics, never certainties. Sanitized data:\n"
)

UnavailableCode = Literal[
    "disabled",
    "invalid_config",
    "invalid_request",
    "dependency_missing",
    "timeout",
    "provider_error",
    "invalid_response",
]
AdvisoryStatus = Literal["available", "unavailable"]

_UNAVAILABLE_MESSAGES: dict[UnavailableCode, str] = {
    "disabled": "Databricks advisory critic is disabled.",
    "invalid_config": "Databricks advisory configuration is invalid.",
    "invalid_request": "No safe candidate summary was available for advisory review.",
    "dependency_missing": "Optional Databricks advisory dependencies are not installed.",
    "timeout": "Databricks advisory review exceeded its time limit.",
    "provider_error": "Databricks advisory review is temporarily unavailable.",
    "invalid_response": "Databricks returned an unusable advisory response.",
}


class _ResponsesApi(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _DatabricksClient(Protocol):
    responses: _ResponsesApi


class _DependencyMissingError(RuntimeError):
    pass


class _InvalidResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabricksAdvisoryConfig:
    """Noncredential configuration for the optional advisory provider."""

    enabled: bool = False
    host: str | None = None
    model: str | None = None
    timeout_seconds: float = 8.0
    max_output_tokens: int = _MAX_OUTPUT_TOKENS
    cache_ttl_seconds: float = 30.0
    cache_max_entries: int = 64

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> DatabricksAdvisoryConfig:
        """Load namespaced noncredential settings; authentication stays with the SDK."""

        values = os.environ if environ is None else environ
        enabled = str(values.get(_ENV_ENABLED, "")).strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        timeout = _environment_float(values.get(_ENV_TIMEOUT), 8.0)
        return cls(
            enabled=enabled,
            host=values.get(_ENV_HOST),
            model=values.get(_ENV_MODEL),
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class AdvisoryCandidateSummary:
    """Identity-free, deterministic metrics for one immutable candidate ordinal."""

    ordinal: int
    position: str
    overall_score: float
    value_score: float | None
    roster_construction_score: float | None
    draft_dynamics_score: float | None
    opponent_model_score: float | None
    risk_news_score: float | None
    scenario_score: float | None
    return_probability: float | None
    risk_status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "position": self.position,
            "overallScore": self.overall_score,
            "valueScore": self.value_score,
            "rosterConstructionScore": self.roster_construction_score,
            "draftDynamicsScore": self.draft_dynamics_score,
            "opponentModelScore": self.opponent_model_score,
            "riskNewsScore": self.risk_news_score,
            "scenarioScore": self.scenario_score,
            "returnProbability": self.return_probability,
            "riskStatus": self.risk_status,
        }


@dataclass(frozen=True)
class DatabricksAdvisoryRequest:
    """Strict allowlist derived from an already-deterministic recommendation result."""

    candidates: tuple[AdvisoryCandidateSummary, ...]
    roster_position_counts: tuple[tuple[str, int], ...]
    current_overall_pick: int | None
    next_user_pick: int | None
    recent_pick_positions: tuple[str, ...]
    quality_flags: tuple[str, ...]

    @classmethod
    def from_recommendation(
        cls, recommendation: Mapping[str, Any]
    ) -> DatabricksAdvisoryRequest:
        """Copy only fixed safe fields; all identities and free-form context are dropped."""

        capabilities = recommendation.get("capabilities")
        capability_mapping = capabilities if isinstance(capabilities, Mapping) else {}
        injury_capability = capability_mapping.get("injuryStatus") is True
        news_capability = capability_mapping.get("externalNews") is True
        candidates: list[AdvisoryCandidateSummary] = []
        raw_recommendations = recommendation.get("recommendations")
        if isinstance(raw_recommendations, Sequence) and not isinstance(
            raw_recommendations, (str, bytes)
        ):
            for raw in raw_recommendations[:_MAX_CANDIDATES]:
                if not isinstance(raw, Mapping):
                    continue
                player = raw.get("player")
                player_mapping = player if isinstance(player, Mapping) else {}
                position = _position(player_mapping.get("position"))
                overall_score = _score(raw.get("overallScore"))
                if not position or overall_score is None:
                    continue
                scores = raw.get("scores")
                score_mapping = scores if isinstance(scores, Mapping) else {}
                risk = raw.get("risk")
                risk_mapping = risk if isinstance(risk, Mapping) else {}
                injury_evidence = bool(
                    injury_capability
                    and risk_mapping.get("available") is True
                    and risk_mapping.get("fresh") is True
                    and risk_mapping.get("injuryFresh") is True
                )
                news_evidence = bool(
                    news_capability
                    and risk_mapping.get("available") is True
                    and risk_mapping.get("fresh") is True
                    and risk_mapping.get("newsFresh") is True
                )
                candidates.append(
                    AdvisoryCandidateSummary(
                        ordinal=len(candidates) + 1,
                        position=position,
                        overall_score=overall_score,
                        value_score=_score(score_mapping.get("value")),
                        roster_construction_score=_score(
                            score_mapping.get("rosterConstruction")
                        ),
                        draft_dynamics_score=_score(score_mapping.get("draftDynamics")),
                        opponent_model_score=_score(score_mapping.get("opponentModel")),
                        risk_news_score=(
                            _score(score_mapping.get("riskNews"))
                            if injury_evidence or news_evidence
                            else None
                        ),
                        scenario_score=_score(score_mapping.get("scenario")),
                        return_probability=_probability(raw.get("returnProbability")),
                        risk_status=(
                            _risk_status(risk_mapping.get("status"))
                            if injury_evidence
                            else "unknown"
                        ),
                    )
                )

        state = recommendation.get("state")
        state_mapping = state if isinstance(state, Mapping) else {}
        roster_counts: Counter[str] = Counter()
        raw_roster = state_mapping.get("userRoster")
        if isinstance(raw_roster, Sequence) and not isinstance(raw_roster, (str, bytes)):
            for player in raw_roster[:32]:
                if not isinstance(player, Mapping):
                    continue
                position = _position(player.get("position"))
                if position:
                    roster_counts[position] += 1

        recent_positions: list[str] = []
        raw_picks = state_mapping.get("picks")
        if isinstance(raw_picks, Sequence) and not isinstance(raw_picks, (str, bytes)):
            for pick in raw_picks[-_MAX_RECENT_POSITIONS:]:
                if not isinstance(pick, Mapping):
                    continue
                position = _position(pick.get("position"))
                if position:
                    recent_positions.append(position)

        flags: set[str] = set()
        health = state_mapping.get("health")
        health_mapping = health if isinstance(health, Mapping) else {}
        if health_mapping.get("fresh") is False:
            flags.add("state_stale")
        if health_mapping.get("teamCountSource") not in {None, "league"}:
            flags.add("inferred_team_count")

        critic = recommendation.get("critic")
        critic_mapping = critic if isinstance(critic, Mapping) else {}
        checks = critic_mapping.get("checks")
        check_mapping = checks if isinstance(checks, Mapping) else {}
        if check_mapping.get("allDraftedPlayersResolved") is False:
            flags.add("unresolved_drafted_players")
        if (
            capability_mapping.get("rosterSlotsAvailable") is False
            or check_mapping.get("rosterSlotsAvailable") is False
            or check_mapping.get("yahooRosterSlotsAvailable") is False
        ):
            flags.add("roster_slots_unavailable")

        if capability_mapping.get("injuryStatus") is False:
            flags.add("injury_status_unavailable")
        if capability_mapping.get("externalNews") is False:
            flags.add("external_news_unavailable")
        if capability_mapping.get("rosterSlotsAvailable") is False:
            flags.add("roster_slots_unavailable")

        return cls(
            candidates=tuple(candidates),
            roster_position_counts=tuple(sorted(roster_counts.items())),
            current_overall_pick=_pick_number(state_mapping.get("currentOverallPick")),
            next_user_pick=_pick_number(state_mapping.get("nextUserPick")),
            recent_pick_positions=tuple(recent_positions),
            quality_flags=tuple(sorted(flags & _QUALITY_FLAGS)),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the exact JSON-safe payload permitted to leave the process."""

        return {
            "schemaVersion": _SCHEMA_VERSION,
            "candidates": [candidate.to_payload() for candidate in self.candidates],
            "rosterPositionCounts": dict(self.roster_position_counts),
            "currentOverallPick": self.current_overall_pick,
            "nextUserPick": self.next_user_pick,
            "recentPickPositions": list(self.recent_pick_positions),
            "qualityFlags": list(self.quality_flags),
            "probabilityCalibration": "uncalibrated",
        }


@dataclass(frozen=True)
class DatabricksAdvisoryUnavailableReason:
    """Machine-readable fail-open reason with a fixed, nonprovider-derived message."""

    code: UnavailableCode
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class DatabricksAdvisoryResult:
    """Advisory-only output that has no fields capable of changing candidate order."""

    status: AdvisoryStatus
    model: str | None
    summary: str | None = None
    cautions: tuple[str, ...] = ()
    cached: bool = False
    latency_ms: float = 0.0
    unavailable_reason: DatabricksAdvisoryUnavailableReason | None = None

    @classmethod
    def available(
        cls,
        *,
        model: str,
        summary: str,
        cautions: Sequence[str] = (),
        cached: bool = False,
        latency_ms: float = 0.0,
    ) -> DatabricksAdvisoryResult:
        """Create an available result while bounding all provider-authored text."""

        safe_model = _validated_model(model)
        safe_summary = _safe_output_text(summary, _MAX_SUMMARY_CHARS)
        safe_cautions = tuple(
            caution
            for caution in (
                _safe_output_text(value, _MAX_CAUTION_CHARS)
                for value in list(cautions)[:_MAX_CAUTIONS]
            )
            if caution
        )
        if safe_model is None or not safe_summary:
            raise ValueError("available advisory results require a valid model and summary")
        return cls(
            status="available",
            model=safe_model,
            summary=safe_summary,
            cautions=safe_cautions,
            cached=bool(cached),
            latency_ms=_latency(latency_ms),
        )

    @classmethod
    def unavailable(
        cls,
        code: UnavailableCode,
        *,
        model: str | None = None,
        cached: bool = False,
        latency_ms: float = 0.0,
    ) -> DatabricksAdvisoryResult:
        """Create a bounded failure result without exposing exception or provider text."""

        return cls(
            status="unavailable",
            model=_validated_model(model),
            cached=bool(cached),
            latency_ms=_latency(latency_ms),
            unavailable_reason=DatabricksAdvisoryUnavailableReason(
                code=code,
                message=_UNAVAILABLE_MESSAGES[code],
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "provider": _PROVIDER,
            "model": self.model,
            "advisoryOnly": True,
            "cached": self.cached,
            "latencyMs": _latency(self.latency_ms),
        }
        if self.status == "available":
            result["summary"] = self.summary or ""
            result["cautions"] = list(self.cautions)
        elif self.unavailable_reason is not None:
            result["unavailableReason"] = self.unavailable_reason.to_dict()
        return result


@dataclass(frozen=True)
class _CacheEntry:
    result: DatabricksAdvisoryResult
    expires_at: float


class DatabricksAdvisoryCritic:
    """Run a short, optional Databricks advisory review with fail-open semantics."""

    def __init__(
        self,
        config: DatabricksAdvisoryConfig | None = None,
        *,
        client_factory: Callable[[str], _DatabricksClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or DatabricksAdvisoryConfig.from_env()
        self._client_factory = client_factory or _default_client_factory
        self._clock = clock
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[DatabricksAdvisoryResult]] = {}
        self._lock = asyncio.Lock()
        self._sdk_slots = asyncio.Semaphore(_MAX_ACTIVE_SDK_CALLS)
        self._sdk_tasks: set[asyncio.Task[Any]] = set()
        self._client: _DatabricksClient | None = None
        self._client_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether configuration opted into the network-backed advisory path."""

        return self._config.enabled is True

    @property
    def model(self) -> str | None:
        """Return only a validated bounded model identifier."""

        return _validated_model(self._config.model)

    @property
    def timeout_seconds(self) -> float:
        """Return a safe provider deadline for service-level defense in depth."""

        return _bounded_number(
            self._config.timeout_seconds, minimum=0.01, maximum=8.0
        ) or 8.0

    async def critique(
        self, request: DatabricksAdvisoryRequest
    ) -> DatabricksAdvisoryResult:
        """Return bounded advisory text or a typed fail-open unavailable result."""

        if not self.enabled:
            return DatabricksAdvisoryResult.unavailable("disabled", model=self.model)

        configuration = self._validated_configuration()
        if configuration is None:
            return DatabricksAdvisoryResult.unavailable("invalid_config", model=self.model)
        host, model, timeout_seconds, max_output_tokens, cache_ttl, cache_size = configuration
        if not request.candidates:
            return DatabricksAdvisoryResult.unavailable("invalid_request", model=model)

        payload = request.to_payload()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        prompt = _prompt(canonical)
        reasoning = {"effort": "low"}
        text_config = _response_text_config()
        cache_contract = json.dumps(
            {
                "model": model,
                "input": prompt,
                "maxOutputTokens": max_output_tokens,
                "reasoning": reasoning,
                "text": text_config,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_key = hashlib.sha256(cache_contract.encode()).hexdigest()

        async with self._lock:
            cached = self._cache.get(cache_key)
            now = self._clock()
            if cached is not None and cached.expires_at > now:
                self._cache.move_to_end(cache_key)
                return replace(cached.result, cached=True, latency_ms=0.0)
            if cached is not None:
                self._cache.pop(cache_key, None)

            task = self._inflight.get(cache_key)
            created = task is None
            if task is None:
                self._prune_completed_inflight()
                if len(self._inflight) >= _MAX_INFLIGHT_REQUESTS:
                    return DatabricksAdvisoryResult.unavailable(
                        "provider_error", model=model
                    )
                task = asyncio.create_task(
                    self._produce(
                        cache_key=cache_key,
                        prompt=prompt,
                        reasoning=reasoning,
                        text_config=text_config,
                        host=host,
                        model=model,
                        timeout_seconds=timeout_seconds,
                        max_output_tokens=max_output_tokens,
                        cache_ttl=cache_ttl,
                        cache_size=cache_size,
                    )
                )
                self._inflight[cache_key] = task
                task.add_done_callback(
                    lambda done, key=cache_key: self._on_produce_done(key, done)
                )

        result = await asyncio.shield(task)
        return result if created else replace(result, cached=True)

    def _prune_completed_inflight(self) -> None:
        for key, task in tuple(self._inflight.items()):
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    def _on_produce_done(
        self,
        cache_key: str,
        task: asyncio.Task[DatabricksAdvisoryResult],
    ) -> None:
        if self._inflight.get(cache_key) is task:
            self._inflight.pop(cache_key, None)
        if not task.cancelled():
            task.exception()

    def _validated_configuration(
        self,
    ) -> tuple[str, str, float, int, float, int] | None:
        host = _validated_host(self._config.host)
        model = self.model
        timeout = _bounded_number(self._config.timeout_seconds, minimum=0.01, maximum=8.0)
        if host is None or model is None or timeout is None:
            return None
        max_output_tokens = _MAX_OUTPUT_TOKENS
        cache_ttl = _bounded_number(
            self._config.cache_ttl_seconds, minimum=1.0, maximum=300.0
        )
        cache_size = _bounded_int(
            self._config.cache_max_entries, minimum=1, maximum=256, default=64
        )
        if cache_ttl is None:
            return None
        return host, model, timeout, max_output_tokens, cache_ttl, cache_size

    async def _produce(
        self,
        *,
        cache_key: str,
        prompt: str,
        reasoning: dict[str, str],
        text_config: dict[str, Any],
        host: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        cache_ttl: float,
        cache_size: int,
    ) -> DatabricksAdvisoryResult:
        started = self._clock()
        try:
            response = await asyncio.wait_for(
                self._request_with_sdk_slot(
                    host,
                    model,
                    prompt,
                    reasoning,
                    text_config,
                    timeout_seconds,
                    max_output_tokens,
                ),
                timeout=timeout_seconds,
            )
            summary, cautions = _parse_response(response)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return DatabricksAdvisoryResult.unavailable(
                "timeout", model=model, latency_ms=(self._clock() - started) * 1000.0
            )
        except _DependencyMissingError:
            return DatabricksAdvisoryResult.unavailable(
                "dependency_missing",
                model=model,
                latency_ms=(self._clock() - started) * 1000.0,
            )
        except _InvalidResponseError:
            return DatabricksAdvisoryResult.unavailable(
                "invalid_response",
                model=model,
                latency_ms=(self._clock() - started) * 1000.0,
            )
        except Exception:
            return DatabricksAdvisoryResult.unavailable(
                "provider_error",
                model=model,
                latency_ms=(self._clock() - started) * 1000.0,
            )

        result = DatabricksAdvisoryResult.available(
            model=model,
            summary=summary,
            cautions=cautions,
            latency_ms=_latency((self._clock() - started) * 1000.0),
        )
        async with self._lock:
            self._cache[cache_key] = _CacheEntry(
                result=result,
                expires_at=self._clock() + cache_ttl,
            )
            self._cache.move_to_end(cache_key)
            while len(self._cache) > cache_size:
                self._cache.popitem(last=False)
        return result

    async def _request_with_sdk_slot(
        self,
        host: str,
        model: str,
        prompt: str,
        reasoning: dict[str, str],
        text_config: dict[str, Any],
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> Any:
        await self._sdk_slots.acquire()
        worker: asyncio.Task[Any] | None = None
        try:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._request_sync,
                    host,
                    model,
                    prompt,
                    reasoning,
                    text_config,
                    timeout_seconds,
                    max_output_tokens,
                )
            )
            self._sdk_tasks.add(worker)
            worker.add_done_callback(self._on_sdk_done)
        except BaseException:
            self._sdk_slots.release()
            raise
        return await asyncio.shield(worker)

    def _on_sdk_done(self, task: asyncio.Task[Any]) -> None:
        self._sdk_tasks.discard(task)
        self._sdk_slots.release()
        if not task.cancelled():
            task.exception()

    def _request_sync(
        self,
        host: str,
        model: str,
        prompt: str,
        reasoning: dict[str, str],
        text_config: dict[str, Any],
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> Any:
        client = self._get_client(host)
        return client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_output_tokens,
            reasoning=reasoning,
            text=text_config,
            timeout=timeout_seconds,
        )

    def _get_client(self, host: str) -> _DatabricksClient:
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                try:
                    self._client = self._client_factory(host)
                except (ImportError, ModuleNotFoundError) as exc:
                    raise _DependencyMissingError from exc
            return self._client


def _default_client_factory(host: str) -> _DatabricksClient:
    """Lazily import optional SDKs and let WorkspaceClient handle unified auth."""

    try:
        sdk = importlib.import_module("databricks.sdk")
        openai_bridge = importlib.import_module("databricks_openai")
        workspace_client_type = sdk.WorkspaceClient
        databricks_openai_type = openai_bridge.DatabricksOpenAI
    except (ImportError, ModuleNotFoundError, AttributeError) as exc:
        raise _DependencyMissingError from exc
    workspace_client = workspace_client_type(host=host)
    return cast(
        _DatabricksClient,
        databricks_openai_type(workspace_client=workspace_client),
    )


def _prompt(canonical_payload: str) -> str:
    return f"{_PROMPT_PREFIX}{canonical_payload}"


def _response_text_config() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "draft_advisory",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": _MAX_SUMMARY_CHARS,
                    },
                    "cautions": {
                        "type": "array",
                        "maxItems": _MAX_CAUTIONS,
                        "items": {
                            "type": "string",
                            "maxLength": _MAX_CAUTION_CHARS,
                        },
                    },
                },
                "required": ["summary", "cautions"],
                "additionalProperties": False,
            },
        }
    }


def _parse_response(response: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(response, Mapping):
        raw = response.get("output_text")
    else:
        raw = getattr(response, "output_text", None)
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_RAW_RESPONSE_CHARS:
        raise _InvalidResponseError
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _InvalidResponseError from exc
    if not isinstance(payload, Mapping) or set(payload) != {"summary", "cautions"}:
        raise _InvalidResponseError
    summary = _safe_output_text(payload.get("summary"), _MAX_SUMMARY_CHARS)
    raw_cautions = payload.get("cautions")
    if not summary or not isinstance(raw_cautions, list):
        raise _InvalidResponseError
    cautions: list[str] = []
    for raw_caution in raw_cautions[:8]:
        caution = _safe_output_text(raw_caution, _MAX_CAUTION_CHARS)
        if caution:
            cautions.append(caution)
        if len(cautions) == _MAX_CAUTIONS:
            break
    return summary, tuple(cautions)


def _safe_output_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    result = html.unescape(value)
    result = _URL_RE.sub("", result)
    result = _SENSITIVE_VALUE_RE.sub("[redacted]", result)
    result = "".join(character for character in result if character.isprintable())
    result = _WHITESPACE_RE.sub(" ", result).strip()
    return result[:limit].rstrip()


def _validated_host(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port not in {None, 443}
    ):
        return None
    if not (
        hostname.endswith(".databricks.com")
        or hostname.endswith(".azuredatabricks.net")
    ):
        return None
    return f"https://{hostname}"


def _validated_model(value: Any) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    if not 1 <= len(value) <= _MAX_MODEL_CHARS or _MODEL_RE.fullmatch(value) is None:
        return None
    return value


def _position(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = _POSITION_ALIASES.get(value.strip().upper(), value.strip().upper())
    return normalized if normalized in _POSITIONS else ""


def _risk_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = _WHITESPACE_RE.sub(" ", value.strip().casefold())
    return normalized if normalized in _RISK_STATUSES else "unknown"


def _score(value: Any) -> float | None:
    return _rounded_bounded_float(value, minimum=0.0, maximum=100.0, digits=2)


def _probability(value: Any) -> float | None:
    return _rounded_bounded_float(value, minimum=0.0, maximum=1.0, digits=4)


def _rounded_bounded_float(
    value: Any, *, minimum: float, maximum: float, digits: int
) -> float | None:
    number = _bounded_number(value, minimum=minimum, maximum=maximum)
    return round(number, digits) if number is not None else None


def _bounded_number(value: Any, *, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return max(minimum, min(maximum, number))


def _bounded_int(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, number))


def _pick_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 1 <= number <= 1_000 else None


def _environment_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _latency(value: Any) -> float:
    bounded = _bounded_number(value, minimum=0.0, maximum=60_000.0)
    return round(bounded or 0.0, 3)


__all__ = [
    "AdvisoryCandidateSummary",
    "DatabricksAdvisoryConfig",
    "DatabricksAdvisoryCritic",
    "DatabricksAdvisoryRequest",
    "DatabricksAdvisoryResult",
    "DatabricksAdvisoryUnavailableReason",
    "UnavailableCode",
]
