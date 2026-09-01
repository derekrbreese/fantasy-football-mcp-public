"""Private persistent request accounting for the FantasyPros public API."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the in-process lock.
    fcntl = None  # type: ignore[assignment]


DEFAULT_REQUEST_BUDGET_PATH = (
    Path.home() / ".fantasy-football-mcp" / "fantasypros-request-budget.json"
)
# Keep a small safety margin below the published 100-request daily ceiling.
DEFAULT_DAILY_REQUEST_LIMIT = 95
_MAX_STATE_BYTES = 4_096
_STATE_FIELDS = frozenset({"schemaVersion", "utcDate", "requestCount"})
_PROCESS_LOCK = threading.Lock()
_LOCK_WAIT_SECONDS = 0.25
_LOCK_RETRY_SECONDS = 0.01


class FantasyProsRequestBudgetError(RuntimeError):
    """Base class for sanitized, fail-closed request-budget errors."""


class FantasyProsRequestBudgetExhausted(FantasyProsRequestBudgetError):
    """Raised when the conservative UTC-day request allowance is consumed."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__("FantasyPros daily request budget is exhausted")
        self.retry_at = retry_at


class FantasyProsRequestBudgetUnavailable(FantasyProsRequestBudgetError):
    """Raised when request accounting cannot be read or persisted safely."""

    def __init__(self) -> None:
        super().__init__("FantasyPros daily request budget is unavailable")


class FantasyProsDailyRequestBudget:
    """Atomically reserve outbound calls using only UTC date/count metadata."""

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        daily_limit: int = DEFAULT_DAILY_REQUEST_LIMIT,
    ) -> None:
        self._path = Path(path).expanduser() if path is not None else None
        try:
            requested_limit = int(daily_limit)
        except (TypeError, ValueError):
            requested_limit = DEFAULT_DAILY_REQUEST_LIMIT
        self._daily_limit = max(1, min(requested_limit, DEFAULT_DAILY_REQUEST_LIMIT))

    @property
    def path(self) -> Path:
        # Resolve the module default lazily so tests can safely inject it.
        return self._path or DEFAULT_REQUEST_BUDGET_PATH

    def reserve(self, now: datetime) -> None:
        """Persist one reservation before its corresponding network request."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise FantasyProsRequestBudgetUnavailable
        utc_now = now.astimezone(timezone.utc)
        destination = self.path
        try:
            with _PROCESS_LOCK:
                self._prepare_directory(
                    destination,
                    tighten_existing=self._path is None,
                )
                with self._exclusive_lock(destination):
                    stored_date, request_count = self._read_state(destination)
                    today = utc_now.date()
                    if stored_date is not None and stored_date > today:
                        raise FantasyProsRequestBudgetUnavailable
                    if stored_date != today:
                        request_count = 0
                    if request_count >= self._daily_limit:
                        raise FantasyProsRequestBudgetExhausted(self._next_day(today))
                    self._write_state(destination, today, request_count + 1)
        except (FantasyProsRequestBudgetExhausted, FantasyProsRequestBudgetUnavailable):
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise FantasyProsRequestBudgetUnavailable from error

    @staticmethod
    def _next_day(today: date) -> datetime:
        return datetime.combine(today + timedelta(days=1), datetime_time(), timezone.utc)

    @staticmethod
    def _prepare_directory(destination: Path, *, tighten_existing: bool) -> None:
        parent = destination.parent
        if parent.is_symlink():
            raise FantasyProsRequestBudgetUnavailable
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            if not parent.is_dir():
                raise FantasyProsRequestBudgetUnavailable from None
            created = False
        if created or tighten_existing:
            parent.chmod(0o700)
        if destination.is_symlink():
            raise FantasyProsRequestBudgetUnavailable

    @contextmanager
    def _exclusive_lock(self, destination: Path) -> Iterator[None]:
        lock_path = destination.with_name(f".{destination.name}.lock")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                deadline = time.monotonic() + _LOCK_WAIT_SECONDS
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0.0:
                            raise FantasyProsRequestBudgetUnavailable from None
                        time.sleep(min(_LOCK_RETRY_SECONDS, remaining))
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_state(self, destination: Path) -> tuple[date | None, int]:
        if not destination.exists():
            return None, 0
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.stat().st_size > _MAX_STATE_BYTES
        ):
            raise FantasyProsRequestBudgetUnavailable
        destination.chmod(0o600)
        raw = destination.read_text(encoding="utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
            raise FantasyProsRequestBudgetUnavailable
        if value.get("schemaVersion") != 1:
            raise FantasyProsRequestBudgetUnavailable
        raw_date = value.get("utcDate")
        request_count = value.get("requestCount")
        if not isinstance(raw_date, str) or type(request_count) is not int:
            raise FantasyProsRequestBudgetUnavailable
        try:
            stored_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise FantasyProsRequestBudgetUnavailable from error
        if stored_date.isoformat() != raw_date or not 0 <= request_count <= 100:
            raise FantasyProsRequestBudgetUnavailable
        return stored_date, request_count

    @staticmethod
    def _write_state(destination: Path, today: date, request_count: int) -> None:
        handle, temporary_name = tempfile.mkstemp(
            prefix=".fantasypros-budget-",
            suffix=".json",
            dir=destination.parent,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temporary:
                json.dump(
                    {
                        "schemaVersion": 1,
                        "utcDate": today.isoformat(),
                        "requestCount": request_count,
                    },
                    temporary,
                    indent=2,
                    sort_keys=True,
                )
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
            destination.chmod(0o600)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


__all__ = [
    "DEFAULT_DAILY_REQUEST_LIMIT",
    "DEFAULT_REQUEST_BUDGET_PATH",
    "FantasyProsDailyRequestBudget",
    "FantasyProsRequestBudgetError",
    "FantasyProsRequestBudgetExhausted",
    "FantasyProsRequestBudgetUnavailable",
]
