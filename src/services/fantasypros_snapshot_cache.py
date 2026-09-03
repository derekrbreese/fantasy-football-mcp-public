"""Private, bounded persistence for normalized provider snapshots."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROVIDER_SNAPSHOT_CACHE_PATH = (
    Path.home() / ".fantasy-football-mcp" / "provider-snapshots.sqlite3"
)
LEGACY_FANTASYPROS_SNAPSHOT_CACHE_PATH = (
    Path.home() / ".fantasy-football-mcp" / "fantasypros-snapshots.sqlite3"
)
# Backward-compatible import name used by callers and tests.
DEFAULT_SNAPSHOT_CACHE_PATH = DEFAULT_PROVIDER_SNAPSHOT_CACHE_PATH

_SCHEMA_VERSION = 4
_MAX_SNAPSHOTS = 16
_MAX_SNAPSHOT_BYTES = 2_000_000
_MAX_TOTAL_RECORD_BYTES = 8_000_000
_MAX_DATABASE_BYTES = 16_777_216
_BUSY_TIMEOUT_MILLISECONDS = 250
_ENDPOINTS = frozenset(
    {"players", "injuries", "news", "projections", "adp", "sleeper_players"}
)
_VARIANTS = {
    "players": frozenset({"catalog", "catalog-season"}),
    "injuries": frozenset({"weekly"}),
    "news": frozenset({"recent"}),
    "projections": frozenset({"preseason-std", "preseason-half", "preseason-ppr"}),
    "adp": frozenset({"preseason-std", "preseason-half", "preseason-ppr"}),
    "sleeper_players": frozenset({"active"}),
}
_RECORD_LIMITS = {
    "players": 5_000,
    "injuries": 2_000,
    "news": 100,
    "projections": 5_000,
    "adp": 5_000,
    "sleeper_players": 10_000,
}
_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DST"})
_STATUSES = frozenset(
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
_NEWS_CATEGORIES = frozenset(
    {"injury", "breaking", "transaction", "rumor", "recap", "news", "commentary", "other"}
)


class FantasyProsSnapshotCacheUnavailable(RuntimeError):
    """Raised when the optional snapshot cache cannot be used safely."""

    def __init__(self) -> None:
        super().__init__("FantasyPros snapshot cache is unavailable")


@dataclass(frozen=True)
class FantasyProsSnapshotKey:
    """Allowlisted structured request identity; never a URL or query string."""

    endpoint: str
    variant: str
    season: int = 0
    week: int = 0
    request_limit: int = 0
    record_limit: int = 0

    def __post_init__(self) -> None:
        valid = self.endpoint in _ENDPOINTS and self.variant in _VARIANTS.get(
            self.endpoint, ()
        )
        valid = valid and type(self.season) is int and type(self.week) is int
        valid = valid and type(self.request_limit) is int and type(self.record_limit) is int
        valid = valid and 1 <= self.record_limit <= _RECORD_LIMITS.get(self.endpoint, 0)
        if self.endpoint == "players":
            if self.variant == "catalog":
                valid = valid and self.season == self.week == self.request_limit == 0
            else:
                valid = (
                    valid
                    and 2012 <= self.season <= 2100
                    and self.week == self.request_limit == 0
                )
        elif self.endpoint == "injuries":
            valid = (
                valid
                and 2012 <= self.season <= 2100
                and 0 <= self.week <= 25
                and self.request_limit == 0
            )
        elif self.endpoint == "news":
            valid = (
                valid
                and self.season == self.week == 0
                and 1 <= self.request_limit <= 100
            )
        elif self.endpoint in {"projections", "adp"}:
            valid = (
                valid
                and 2012 <= self.season <= 2100
                and self.week == 0
                and self.request_limit == 0
            )
        elif self.endpoint == "sleeper_players":
            valid = (
                valid
                and self.variant == "active"
                and self.season == self.week == self.request_limit == 0
            )
        if not valid:
            raise ValueError("invalid FantasyPros snapshot key")


@dataclass(frozen=True)
class FantasyProsSnapshot:
    """One normalized provider snapshot and bounded coverage metadata."""

    records: tuple[dict[str, Any], ...]
    fetched_at: datetime
    truncated: bool
    returned_count: int
    reported_count: int | None
    reported_limit: int | None
    public_api_limited: bool


class FantasyProsSnapshotCache:
    """Persist only strict normalized snapshot rows in a private SQLite file."""

    def __init__(self, *, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path is not None else None
        self._legacy_migration_checked = False

    @property
    def path(self) -> Path:
        # Resolve lazily so tests and embedders can inject the module default.
        return self._path or DEFAULT_SNAPSHOT_CACHE_PATH

    def load(self, key: FantasyProsSnapshotKey) -> FantasyProsSnapshot | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT fetched_at, records_json, truncated, returned_count,
                           reported_count, reported_limit, public_api_limited
                    FROM snapshots
                    WHERE endpoint = ? AND variant = ? AND season = ? AND week = ?
                      AND request_limit = ? AND record_limit = ?
                    """,
                    (
                        key.endpoint,
                        key.variant,
                        key.season,
                        key.week,
                        key.request_limit,
                        key.record_limit,
                    ),
                ).fetchone()
            if row is None:
                return None
            return self._decode_snapshot(key, row)
        except FantasyProsSnapshotCacheUnavailable:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
            raise FantasyProsSnapshotCacheUnavailable from error

    def save(
        self,
        key: FantasyProsSnapshotKey,
        snapshot: FantasyProsSnapshot,
    ) -> None:
        try:
            encoded = self._encode_snapshot(key, snapshot)
            with self._connection() as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO snapshots (
                            endpoint, variant, season, week, request_limit, record_limit,
                            fetched_at, records_json, truncated, returned_count,
                            reported_count, reported_limit, public_api_limited
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (
                            endpoint, variant, season, week, request_limit, record_limit
                        ) DO UPDATE SET
                            fetched_at = excluded.fetched_at,
                            records_json = excluded.records_json,
                            truncated = excluded.truncated,
                            returned_count = excluded.returned_count,
                            reported_count = excluded.reported_count,
                            reported_limit = excluded.reported_limit,
                            public_api_limited = excluded.public_api_limited
                        """,
                        (
                            key.endpoint,
                            key.variant,
                            key.season,
                            key.week,
                            key.request_limit,
                            key.record_limit,
                            int(snapshot.fetched_at.timestamp()),
                            encoded,
                            int(snapshot.truncated),
                            snapshot.returned_count,
                            snapshot.reported_count,
                            snapshot.reported_limit,
                            int(snapshot.public_api_limited),
                        ),
                    )
                    self._prune(connection)
        except FantasyProsSnapshotCacheUnavailable:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            raise FantasyProsSnapshotCacheUnavailable from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            destination = self.path
            self._prepare_directory(destination, tighten_existing=self._path is None)
            self._migrate_legacy_database(destination)
            self._prepare_file(destination)
            connection = sqlite3.connect(
                destination,
                timeout=_BUSY_TIMEOUT_MILLISECONDS / 1_000,
                isolation_level="DEFERRED",
            )
            with closing(connection):
                connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
                connection.execute("PRAGMA journal_mode = DELETE")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("PRAGMA secure_delete = ON")
                connection.execute("PRAGMA page_size = 4096")
                page_size = connection.execute("PRAGMA page_size").fetchone()
                page_count = connection.execute("PRAGMA page_count").fetchone()
                if (
                    page_size is None
                    or page_count is None
                    or type(page_size[0]) is not int
                    or type(page_count[0]) is not int
                    or page_size[0] <= 0
                    or page_count[0] < 0
                    or page_size[0] * page_count[0] > _MAX_DATABASE_BYTES
                ):
                    raise FantasyProsSnapshotCacheUnavailable
                max_pages = max(1, _MAX_DATABASE_BYTES // page_size[0])
                connection.execute(f"PRAGMA max_page_count = {max_pages}")
                self._initialize_schema(connection)
                yield connection
        except FantasyProsSnapshotCacheUnavailable:
            raise
        except (OSError, sqlite3.Error) as error:
            raise FantasyProsSnapshotCacheUnavailable from error

    def _migrate_legacy_database(self, destination: Path) -> None:
        if self._legacy_migration_checked:
            return
        self._legacy_migration_checked = True
        legacy = LEGACY_FANTASYPROS_SNAPSHOT_CACHE_PATH
        if (
            self._path is not None
            or destination != DEFAULT_PROVIDER_SNAPSHOT_CACHE_PATH
            or destination.exists()
            or not legacy.exists()
            or legacy == destination
        ):
            return
        if (
            legacy.is_symlink()
            or not legacy.is_file()
            or legacy.stat().st_nlink != 1
            or legacy.stat().st_size > _MAX_DATABASE_BYTES
        ):
            raise FantasyProsSnapshotCacheUnavailable
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".provider-snapshots-migration-",
            suffix=".sqlite3",
            dir=destination.parent,
        )
        os.close(descriptor)
        try:
            source_uri = f"{legacy.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(source_uri, uri=True)) as source:
                with closing(sqlite3.connect(temporary_name)) as target:
                    source.backup(target)
                    target.commit()
            with open(temporary_name, "rb") as migrated:
                os.fsync(migrated.fileno())
            os.chmod(temporary_name, 0o600)
            try:
                os.link(temporary_name, destination)
            except FileExistsError:
                # Another provider process completed the same first-use migration.
                pass
            else:
                destination.chmod(0o600)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _prepare_directory(destination: Path, *, tighten_existing: bool) -> None:
        parent = destination.parent
        if parent.is_symlink():
            raise FantasyProsSnapshotCacheUnavailable
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            if not parent.is_dir():
                raise FantasyProsSnapshotCacheUnavailable from None
            created = False
        if created or tighten_existing:
            parent.chmod(0o700)
        if destination.is_symlink():
            raise FantasyProsSnapshotCacheUnavailable

    @staticmethod
    def _prepare_file(destination: Path) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file():
                raise FantasyProsSnapshotCacheUnavailable from None
        else:
            os.close(descriptor)
        metadata = destination.stat()
        if metadata.st_nlink != 1 or metadata.st_size > _MAX_DATABASE_BYTES:
            raise FantasyProsSnapshotCacheUnavailable
        destination.chmod(0o600)

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()
        if version is None or type(version[0]) is not int or version[0] not in (0, 1, 2, 3, 4):
            raise FantasyProsSnapshotCacheUnavailable
        if version[0] in (1, 2, 3):
            with connection:
                FantasyProsSnapshotCache._create_schema(connection, "snapshots_v4")
                connection.execute(
                    """
                    INSERT INTO snapshots_v4
                    SELECT endpoint, variant, season, week, request_limit, record_limit,
                           fetched_at, records_json, truncated, returned_count,
                           reported_count, reported_limit, public_api_limited
                    FROM snapshots
                    """
                )
                connection.execute("DROP TABLE snapshots")
                connection.execute("ALTER TABLE snapshots_v4 RENAME TO snapshots")
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return
        FantasyProsSnapshotCache._create_schema(connection, "snapshots")
        if version[0] == 0:
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.commit()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection, table: str) -> None:
        if table not in {"snapshots", "snapshots_v2", "snapshots_v3", "snapshots_v4"}:
            raise FantasyProsSnapshotCacheUnavailable
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                endpoint TEXT NOT NULL,
                variant TEXT NOT NULL,
                season INTEGER NOT NULL,
                week INTEGER NOT NULL,
                request_limit INTEGER NOT NULL,
                record_limit INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL,
                records_json TEXT NOT NULL CHECK(length(records_json) <= 2000000),
                truncated INTEGER NOT NULL CHECK(truncated IN (0, 1)),
                returned_count INTEGER NOT NULL CHECK(returned_count BETWEEN 0 AND 100000),
                reported_count INTEGER CHECK(reported_count BETWEEN 0 AND 10000000),
                reported_limit INTEGER CHECK(reported_limit BETWEEN 1 AND 100000),
                public_api_limited INTEGER NOT NULL CHECK(public_api_limited IN (0, 1)),
                PRIMARY KEY (
                    endpoint, variant, season, week, request_limit, record_limit
                ),
                CHECK(endpoint IN (
                    'players', 'injuries', 'news', 'projections', 'adp', 'sleeper_players'
                )),
                CHECK(variant IN (
                    'catalog', 'catalog-season', 'weekly', 'recent',
                    'preseason-std', 'preseason-half', 'preseason-ppr', 'active'
                ))
            ) WITHOUT ROWID
            """
        )

    @classmethod
    def _encode_snapshot(
        cls,
        key: FantasyProsSnapshotKey,
        snapshot: FantasyProsSnapshot,
    ) -> str:
        fetched_at = cls._valid_fetched_at(snapshot.fetched_at)
        if type(snapshot.truncated) is not bool or type(snapshot.public_api_limited) is not bool:
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_int(snapshot.returned_count, 0, 100_000):
            raise FantasyProsSnapshotCacheUnavailable
        if snapshot.reported_count is not None and not cls._bounded_int(
            snapshot.reported_count, 0, 10_000_000
        ):
            raise FantasyProsSnapshotCacheUnavailable
        if snapshot.reported_limit is not None and not cls._bounded_int(
            snapshot.reported_limit, 1, 100_000
        ):
            raise FantasyProsSnapshotCacheUnavailable
        records = cls._validate_records(key.endpoint, snapshot.records)
        if len(records) > key.record_limit:
            raise FantasyProsSnapshotCacheUnavailable
        # Validate here even though the caller persists the original timestamp below.
        if fetched_at != snapshot.fetched_at.astimezone(timezone.utc):
            raise FantasyProsSnapshotCacheUnavailable
        encoded = json.dumps(records, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise FantasyProsSnapshotCacheUnavailable
        return encoded

    @classmethod
    def _decode_snapshot(
        cls,
        key: FantasyProsSnapshotKey,
        row: Sequence[Any],
    ) -> FantasyProsSnapshot:
        if len(row) != 7:
            raise FantasyProsSnapshotCacheUnavailable
        fetched_epoch, encoded, truncated, returned, reported, limit, public_limited = row
        if type(fetched_epoch) is not int or not 946_684_800 <= fetched_epoch <= 4_133_980_799:
            raise FantasyProsSnapshotCacheUnavailable
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise FantasyProsSnapshotCacheUnavailable
        if truncated not in (0, 1) or public_limited not in (0, 1):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_int(returned, 0, 100_000):
            raise FantasyProsSnapshotCacheUnavailable
        if reported is not None and not cls._bounded_int(reported, 0, 10_000_000):
            raise FantasyProsSnapshotCacheUnavailable
        if limit is not None and not cls._bounded_int(limit, 1, 100_000):
            raise FantasyProsSnapshotCacheUnavailable
        decoded = json.loads(encoded)
        if not isinstance(decoded, list):
            raise FantasyProsSnapshotCacheUnavailable
        records = cls._validate_records(key.endpoint, decoded)
        if len(records) > key.record_limit:
            raise FantasyProsSnapshotCacheUnavailable
        return FantasyProsSnapshot(
            records=records,
            fetched_at=datetime.fromtimestamp(fetched_epoch, timezone.utc),
            truncated=bool(truncated),
            returned_count=returned,
            reported_count=reported,
            reported_limit=limit,
            public_api_limited=bool(public_limited),
        )

    @classmethod
    def _validate_records(
        cls,
        endpoint: str,
        records: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise FantasyProsSnapshotCacheUnavailable
        if len(records) > _RECORD_LIMITS.get(endpoint, 0):
            raise FantasyProsSnapshotCacheUnavailable
        result: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise FantasyProsSnapshotCacheUnavailable
            copied = dict(record)
            if endpoint == "players":
                cls._validate_player(copied)
            elif endpoint == "injuries":
                cls._validate_injury(copied)
            elif endpoint == "news":
                cls._validate_news(copied)
            elif endpoint == "projections":
                cls._validate_projection(copied)
            elif endpoint == "adp":
                cls._validate_adp(copied)
            elif endpoint == "sleeper_players":
                cls._validate_sleeper_player(copied)
            else:
                raise FantasyProsSnapshotCacheUnavailable
            result.append(copied)
        return tuple(result)

    @classmethod
    def _validate_player(cls, record: dict[str, Any]) -> None:
        base_fields = {"id", "name", "position", "team"}
        if set(record) not in (base_fields, base_fields | {"adpStd", "adpPpr"}):
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_identity_fields(record, allow_empty=False)
        if "adpStd" in record:
            for field in ("adpStd", "adpPpr"):
                value = record[field]
                if value is not None and not cls._bounded_number(value, 0.01, 10_000.0):
                    raise FantasyProsSnapshotCacheUnavailable

    @classmethod
    def _validate_injury(cls, record: dict[str, Any]) -> None:
        if set(record) != {
            "id",
            "name",
            "position",
            "team",
            "status",
            "updatedAt",
        }:
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_identity_fields(record, allow_empty=True)
        if record["status"] not in _STATUSES:
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_timestamp(record["updatedAt"], allow_none=True)

    @classmethod
    def _validate_news(cls, record: dict[str, Any]) -> None:
        if set(record) != {"id", "headline", "category", "publishedAt"}:
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_int(record["id"], 1, 2_147_483_647):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_text(record["headline"], 1, 240):
            raise FantasyProsSnapshotCacheUnavailable
        if record["category"] not in _NEWS_CATEGORIES:
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_timestamp(record["publishedAt"], allow_none=False)

    @classmethod
    def _validate_projection(cls, record: dict[str, Any]) -> None:
        if set(record) != {
            "id",
            "name",
            "position",
            "team",
            "points",
            "opportunities",
            "opportunityKind",
        }:
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_identity_fields(record, allow_empty=False)
        if record["position"] not in {"RB", "WR", "TE"}:
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_number(record["points"], 0.0, 10_000.0):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_number(record["opportunities"], 0.0, 10_000.0):
            raise FantasyProsSnapshotCacheUnavailable
        expected_kind = "touches" if record["position"] == "RB" else "receptions"
        if record["opportunityKind"] != expected_kind:
            raise FantasyProsSnapshotCacheUnavailable

    @classmethod
    def _validate_adp(cls, record: dict[str, Any]) -> None:
        base_fields = {"id", "name", "position", "team", "adp"}
        if set(record) not in (base_fields, base_fields | {"yahooId"}):
            raise FantasyProsSnapshotCacheUnavailable
        cls._validate_identity_fields(record, allow_empty=False)
        if not cls._bounded_number(record["adp"], 0.01, 10_000.0):
            raise FantasyProsSnapshotCacheUnavailable
        if "yahooId" in record:
            yahoo_id = record["yahooId"]
            if (
                not isinstance(yahoo_id, str)
                or not yahoo_id.isdigit()
                or yahoo_id.startswith("0")
                or not 1 <= int(yahoo_id) <= 10_000_000_000
            ):
                raise FantasyProsSnapshotCacheUnavailable

    @classmethod
    def _validate_sleeper_player(cls, record: dict[str, Any]) -> None:
        if set(record) != {
            "sleeperId",
            "name",
            "position",
            "team",
            "yearsExperience",
            "yahooId",
        }:
            raise FantasyProsSnapshotCacheUnavailable
        sleeper_id = record["sleeperId"]
        yahoo_id = record["yahooId"]
        if (
            not isinstance(sleeper_id, str)
            or not sleeper_id.isdigit()
            or sleeper_id.startswith("0")
            or not 1 <= int(sleeper_id) <= 10_000_000_000
        ):
            raise FantasyProsSnapshotCacheUnavailable
        if yahoo_id is not None and (
            not isinstance(yahoo_id, str)
            or not yahoo_id.isdigit()
            or yahoo_id.startswith("0")
            or not 1 <= int(yahoo_id) <= 10_000_000_000
        ):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_text(record["name"], 1, 120):
            raise FantasyProsSnapshotCacheUnavailable
        if record["position"] not in {"RB", "WR", "TE"}:
            raise FantasyProsSnapshotCacheUnavailable
        team = record["team"]
        if not cls._bounded_text(team, 0, 5) or not all(
            character.isupper() or character.isdigit() for character in team
        ):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_int(record["yearsExperience"], 0, 30):
            raise FantasyProsSnapshotCacheUnavailable

    @classmethod
    def _validate_identity_fields(
        cls,
        record: dict[str, Any],
        *,
        allow_empty: bool,
    ) -> None:
        minimum = 0 if allow_empty else 1
        if not cls._bounded_int(record["id"], 1, 2_147_483_647):
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_text(record["name"], minimum, 120):
            raise FantasyProsSnapshotCacheUnavailable
        position = record["position"]
        team = record["team"]
        if position != "" and position not in _POSITIONS:
            raise FantasyProsSnapshotCacheUnavailable
        if not allow_empty and position not in _POSITIONS:
            raise FantasyProsSnapshotCacheUnavailable
        if not cls._bounded_text(team, minimum, 5):
            raise FantasyProsSnapshotCacheUnavailable
        if not all(character.isupper() or character.isdigit() for character in team):
            raise FantasyProsSnapshotCacheUnavailable

    @staticmethod
    def _validate_timestamp(value: Any, *, allow_none: bool) -> None:
        if value is None and allow_none:
            return
        if not isinstance(value, str) or not 20 <= len(value) <= 30 or not value.endswith("Z"):
            raise FantasyProsSnapshotCacheUnavailable
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise FantasyProsSnapshotCacheUnavailable from error
        if parsed.tzinfo is None or parsed.astimezone(timezone.utc).year not in range(2000, 2101):
            raise FantasyProsSnapshotCacheUnavailable

    @staticmethod
    def _valid_fetched_at(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise FantasyProsSnapshotCacheUnavailable
        result = value.astimezone(timezone.utc)
        if result.year not in range(2000, 2101):
            raise FantasyProsSnapshotCacheUnavailable
        return result

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int) -> bool:
        return type(value) is int and minimum <= value <= maximum

    @staticmethod
    def _bounded_number(value: Any, minimum: float, maximum: float) -> bool:
        return (
            type(value) in (int, float)
            and math.isfinite(float(value))
            and minimum <= float(value) <= maximum
        )

    @staticmethod
    def _bounded_text(value: Any, minimum: int, maximum: int) -> bool:
        return isinstance(value, str) and minimum <= len(value) <= maximum

    @staticmethod
    def _prune(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT endpoint, variant, season, week, request_limit, record_limit,
                   length(records_json)
            FROM snapshots
            ORDER BY fetched_at DESC, endpoint, variant, season, week,
                     request_limit, record_limit
            """
        ).fetchall()
        retained_bytes = 0
        stale_keys: list[tuple[Any, ...]] = []
        for index, row in enumerate(rows):
            encoded_size = row[6]
            if type(encoded_size) is not int or encoded_size < 0:
                stale_keys.append(tuple(row[:6]))
                continue
            if index >= _MAX_SNAPSHOTS or retained_bytes + encoded_size > _MAX_TOTAL_RECORD_BYTES:
                stale_keys.append(tuple(row[:6]))
                continue
            retained_bytes += encoded_size
        connection.executemany(
            """
            DELETE FROM snapshots
            WHERE endpoint = ? AND variant = ? AND season = ? AND week = ?
              AND request_limit = ? AND record_limit = ?
            """,
            stale_keys,
        )


# Provider-neutral names for shared-cache consumers. The original names remain
# public so existing callers do not need a coordinated migration.
ProviderSnapshot = FantasyProsSnapshot
ProviderSnapshotCache = FantasyProsSnapshotCache
ProviderSnapshotCacheUnavailable = FantasyProsSnapshotCacheUnavailable
ProviderSnapshotKey = FantasyProsSnapshotKey


__all__ = [
    "DEFAULT_PROVIDER_SNAPSHOT_CACHE_PATH",
    "DEFAULT_SNAPSHOT_CACHE_PATH",
    "FantasyProsSnapshot",
    "FantasyProsSnapshotCache",
    "FantasyProsSnapshotCacheUnavailable",
    "FantasyProsSnapshotKey",
    "LEGACY_FANTASYPROS_SNAPSHOT_CACHE_PATH",
    "ProviderSnapshot",
    "ProviderSnapshotCache",
    "ProviderSnapshotCacheUnavailable",
    "ProviderSnapshotKey",
]
