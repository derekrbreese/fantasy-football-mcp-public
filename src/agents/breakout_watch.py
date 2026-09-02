"""Pure evidence-gated Breakout Watch labels for draft candidates.

Breakout Watch is not inferred from ADP, ranking deltas, or headline text.  It requires
fresh, explicitly sourced projection and opportunity evidence from a local profile and
compares only players at the same position from the same projection source.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from itertools import islice
from typing import Any

MAX_CANDIDATES = 500
MIN_POSITION_COHORT = 5
MAX_EVIDENCE_AGE_DAYS = 45
MAX_FUTURE_SKEW_DAYS = 1
_ELIGIBLE_POSITIONS = {"RB", "WR", "TE"}
_EXPECTED_OPPORTUNITY_KINDS = {
    "RB": {"touches"},
    "WR": {"targets", "receptions"},
    "TE": {"targets", "receptions"},
}
_MIN_PERCENTILE = 0.60
_MAX_BREAKOUT_EXPERIENCE_YEARS = 3
_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()&'+-]{0,79}$")
_EVIDENCE_FIELDS = {
    "source",
    "as_of",
    "projected_points",
    "projected_opportunities",
    "opportunity_kind",
    "experience_years",
}
_UNSAFE_SOURCE = re.compile(
    r"(?:[a-z][a-z0-9+.-]{1,15}://|www\.|"
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|dev|app|test)(?:[/:?#]|$)|"
    r"(?:^|[?&;\s])"
    r"(?:auth(?:orization)?|token|api[_-]?key|key|cookie|session|password|secret)\s*[:=]|"
    r"\?[^\s=]{1,64}=)",
    re.IGNORECASE,
)


def _finite(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        return None
    return result


def _evidence(
    candidate: Mapping[str, Any], index: int, reference_date: date
) -> dict[str, Any] | None:
    position = str(candidate.get("position") or "").strip().upper()
    raw = candidate.get("breakout_evidence")
    if (
        position not in _ELIGIBLE_POSITIONS
        or not isinstance(raw, Mapping)
        or set(raw) != _EVIDENCE_FIELDS
    ):
        return None
    raw_source = raw.get("source")
    if not isinstance(raw_source, str):
        return None
    source = " ".join(unicodedata.normalize("NFKC", raw_source).split())
    if (
        not source
        or len(source) > 80
        or not _SAFE_SOURCE.fullmatch(source)
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in source
        )
        or _UNSAFE_SOURCE.search(source)
    ):
        return None
    raw_as_of = raw.get("as_of")
    try:
        as_of = date.fromisoformat(raw_as_of) if isinstance(raw_as_of, str) else None
    except ValueError:
        as_of = None
    points = _finite(raw.get("projected_points"), 0.01, 1_000.0)
    opportunities = _finite(raw.get("projected_opportunities"), 1.0, 1_000.0)
    experience = raw.get("experience_years")
    if isinstance(experience, bool) or not isinstance(experience, int) or not 0 <= experience <= 30:
        return None
    opportunity_kind = str(raw.get("opportunity_kind") or "").strip().lower()
    if (
        as_of is None
        or points is None
        or opportunities is None
        or opportunity_kind not in _EXPECTED_OPPORTUNITY_KINDS[position]
    ):
        return None
    age = (reference_date - as_of).days
    if age > MAX_EVIDENCE_AGE_DAYS or age < -MAX_FUTURE_SKEW_DAYS:
        return None
    return {
        "index": index,
        "position": position,
        "source": source,
        "asOf": as_of.isoformat(),
        "projectedPoints": points,
        "projectedOpportunities": opportunities,
        "opportunityKind": opportunity_kind,
        "experienceYears": experience,
    }


def _percentile(value: float, population: Sequence[float]) -> float:
    if not population:
        return 0.0
    return sum(item <= value for item in population) / len(population)


def evaluate_breakout_watch(
    rankings: Sequence[Mapping[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return optional labels aligned to at most 500 input ranking rows."""

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    candidates = list(islice(rankings, MAX_CANDIDATES))
    labels: list[dict[str, Any] | None] = [None] * len(candidates)
    records = [
        record
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, Mapping)
        and (record := _evidence(candidate, index, reference.date())) is not None
    ]
    cohorts: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        cohorts[
            (record["position"], record["source"], record["opportunityKind"])
        ].append(record)
    usable = {
        key: values
        for key, values in cohorts.items()
        if len(values) >= MIN_POSITION_COHORT
    }
    for values in usable.values():
        points = [record["projectedPoints"] for record in values]
        opportunities = [record["projectedOpportunities"] for record in values]
        for record in values:
            points_percentile = _percentile(record["projectedPoints"], points)
            opportunity_percentile = _percentile(
                record["projectedOpportunities"], opportunities
            )
            if (
                record["experienceYears"] <= _MAX_BREAKOUT_EXPERIENCE_YEARS
                and points_percentile >= _MIN_PERCENTILE
                and opportunity_percentile >= _MIN_PERCENTILE
            ):
                labels[record["index"]] = {
                    "label": "Breakout Watch",
                    "method": (
                        "fresh same-source position cohort: projected points and "
                        "projected opportunities at or above the 60th percentile, "
                        "with three or fewer years of experience"
                    ),
                    "source": record["source"],
                    "asOf": record["asOf"],
                    "projectedPoints": round(record["projectedPoints"], 2),
                    "projectedOpportunities": round(
                        record["projectedOpportunities"], 2
                    ),
                    "opportunityKind": record["opportunityKind"],
                    "experienceYears": record["experienceYears"],
                    "pointsPercentile": round(points_percentile, 4),
                    "opportunityPercentile": round(opportunity_percentile, 4),
                    "calibrated": False,
                }
    coverage_positions = sorted({position for position, _source, _kind in usable})
    available = bool(usable)
    return {
        "status": "available" if available else "unavailable",
        "method": (
            "explicit fresh sourced projection and opportunity evidence; same-position, "
            "same-source cohort heuristic; ADP and news are excluded"
        ),
        "calibrated": False,
        "coveragePositions": coverage_positions,
        "evidencePlayers": min(len(records), MAX_CANDIDATES),
        "labels": labels,
        "message": (
            "Breakout labels use fresh sourced projection and opportunity evidence and are uncalibrated."
            if available
            else "Breakout evidence is unavailable: import fresh sourced projection and opportunity fields for at least five comparable RB, WR, or TE players."
        ),
    }
