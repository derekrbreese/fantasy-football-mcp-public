"""
Cache manager agent for intelligent data caching with TTL.

This module provides the CacheManagerAgent class that handles all caching
operations for the fantasy football MCP server, including file-based
persistence, TTL management, and cache invalidation strategies.
"""

import asyncio
import json
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple, Protocol, runtime_checkable
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import sys
from collections import OrderedDict
import threading
from abc import ABC, abstractmethod

import aiocache
from aiocache import SimpleMemoryCache, Cache
from aiocache.serializers import PickleSerializer, JsonSerializer
from loguru import logger

from config.settings import Settings


class CacheStrategy(str, Enum):
    """Cache strategy options."""

    MEMORY_ONLY = "memory_only"
    FILE_ONLY = "file_only"
    HYBRID = "hybrid"
    DISTRIBUTED = "distributed"


class EvictionPolicy(str, Enum):
    """Cache eviction policy options."""

    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    TTL = "ttl"  # Time To Live based
    SIZE = "size"  # Size based


class CacheLevel(str, Enum):
    """Cache level priority."""

    L1_MEMORY = "l1_memory"
    L2_FILE = "l2_file"
    L3_DISTRIBUTED = "l3_distributed"


@dataclass
class CacheEntry:
    """Cache entry with metadata."""

    key: str
    value: Any
    created_at: datetime
    expires_at: Optional[datetime]
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    size_bytes: int = 0
    priority: int = 0  # For priority-based eviction

    def __post_init__(self):
        if self.last_accessed is None:
            self.last_accessed = self.created_at

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def time_until_expiry(self) -> Optional[timedelta]:
        """Get time remaining until expiry."""
        if self.expires_at is None:
            return None
        remaining = self.expires_at - datetime.utcnow()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)

    def touch(self):
        """Update access metadata."""
        self.access_count += 1
        self.last_accessed = datetime.utcnow()


class CacheStats:
    """Cache statistics tracking."""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.deletes = 0
        self.evictions = 0
        self.size_bytes = 0
        self.entry_count = 0
        self.start_time = datetime.utcnow()

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def uptime(self) -> timedelta:
        """Get cache uptime."""
        return datetime.utcnow() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "deletes": self.deletes,
            "evictions": self.evictions,
            "hit_rate": self.hit_rate,
            "size_bytes": self.size_bytes,
            "entry_count": self.entry_count,
            "uptime_seconds": self.uptime.total_seconds(),
        }


class CacheManagerAgent:
    """
    Agent responsible for intelligent caching with TTL and persistence.

    This agent handles:
    - Multi-level caching (memory, file-based)
    - TTL-based expiration with clear_expired() method
    - Cache warming and prefetching
    - Smart eviction policies (LRU, LFU, Size-based)
    - Cache statistics and monitoring
    - Tag-based cache invalidation
    - Partial cache invalidation (by league, by week, etc.)
    - Memory-efficient with configurable size limits
    - Thread-safe for concurrent access
    - Enhanced cache key generation for different data types
    """

    def __init__(
        self,
        settings: Settings,
        max_memory_size: int = 100 * 1024 * 1024,  # 100MB default
        eviction_policy: EvictionPolicy = EvictionPolicy.LRU,
    ):
        """
        Initialize the cache manager agent.

        Args:
            settings: Application settings containing cache configuration
            max_memory_size: Maximum memory cache size in bytes
            eviction_policy: Eviction policy for memory management
        """
        self.settings = settings
        self.strategy = CacheStrategy.HYBRID
        self.stats = CacheStats()
        self.max_memory_size = max_memory_size
        self.eviction_policy = eviction_policy

        # Setup cache directories
        self.cache_dir = settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize caches
        self._memory_cache: Optional[Cache] = None
        self._file_cache_path = self.cache_dir / "file_cache"
        self._file_cache_path.mkdir(exist_ok=True)

        # Cache entries tracking with LRU support
        self._entries: Dict[str, CacheEntry] = {}
        self._lru_order: OrderedDict[str, None] = OrderedDict()
        self._tag_index: Dict[str, List[str]] = {}

        # Memory management
        self._current_memory_size = 0

        # Locks for thread safety
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()  # For sync operations
        self._cleanup_task: Optional[asyncio.Task] = None

        logger.info(
            f"CacheManagerAgent initialized with max_memory_size={max_memory_size/1024/1024:.1f}MB, "
            f"eviction_policy={eviction_policy}"
        )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()

    async def initialize(self) -> None:
        """Initialize cache systems."""
        try:
            # Initialize memory cache
            self._memory_cache = SimpleMemoryCache(
                serializer=PickleSerializer(), namespace="yahoo_fantasy_football"
            )

            # Load existing file cache entries
            await self._load_file_cache_index()

            # Start cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            logger.info("Cache manager initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize cache manager: {e}")
            raise

    async def cleanup(self) -> None:
        """Clean up cache resources."""
        try:
            # Cancel cleanup task
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass

            # Save file cache index
            await self._save_file_cache_index()

            # Close memory cache
            if self._memory_cache:
                await self._memory_cache.close()

            logger.info("Cache manager cleaned up")

        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache with intelligent lookup across levels.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        async with self._lock:
            try:
                # Check memory cache first (L1)
                if self._memory_cache:
                    value = await self._memory_cache.get(key)
                    if value is not None:
                        await self._record_hit(key, CacheLevel.L1_MEMORY)
                        return value

                # Check file cache (L2)
                file_value = await self._get_from_file_cache(key)
                if file_value is not None:
                    # Promote to memory cache
                    if self._memory_cache:
                        await self._memory_cache.set(key, file_value)
                    await self._record_hit(key, CacheLevel.L2_FILE)
                    return file_value

                # Record miss
                self.stats.misses += 1
                logger.debug(f"Cache miss for key: {key}")
                return None

            except Exception as e:
                logger.error(f"Error getting cache key {key}: {e}")
                self.stats.misses += 1
                return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[Union[int, timedelta]] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live (seconds or timedelta)
            tags: Optional tags for grouped invalidation

        Returns:
            True if successfully cached
        """
        async with self._lock:
            try:
                # Normalize TTL
                if isinstance(ttl, int):
                    ttl = timedelta(seconds=ttl)
                elif ttl is None:
                    ttl = timedelta(seconds=self.settings.cache_ttl_seconds)

                expires_at = datetime.utcnow() + ttl

                # Create cache entry
                entry = CacheEntry(
                    key=key,
                    value=value,
                    created_at=datetime.utcnow(),
                    expires_at=expires_at,
                    tags=tags or [],
                    size_bytes=self._estimate_size(value),
                )

                # Store in memory cache
                if self._memory_cache:
                    await self._memory_cache.set(key, value, ttl=int(ttl.total_seconds()))

                # Store in file cache for persistence
                await self._set_in_file_cache(key, entry)

                # Check memory limits and evict if necessary
                await self._enforce_memory_limits(entry.size_bytes)

                # Update tracking
                old_entry = self._entries.get(key)
                old_size = old_entry.size_bytes if old_entry else 0

                self._entries[key] = entry
                self._lru_order[key] = None
                self._lru_order.move_to_end(key)
                await self._update_tag_index(key, tags or [])

                # Update memory tracking
                size_delta = entry.size_bytes - old_size
                self._current_memory_size += size_delta

                # Update stats
                self.stats.sets += 1
                self.stats.size_bytes += size_delta
                if not old_entry:
                    self.stats.entry_count += 1

                logger.debug(f"Cached key {key} with TTL {ttl}")
                return True

            except Exception as e:
                logger.error(f"Error setting cache key {key}: {e}")
                return False

    async def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted
        """
        async with self._lock:
            try:
                deleted = False

                # Remove from memory cache
                if self._memory_cache:
                    await self._memory_cache.delete(key)
                    deleted = True

                # Remove from file cache
                if await self._delete_from_file_cache(key):
                    deleted = True

                # Update tracking
                if key in self._entries:
                    entry = self._entries[key]
                    self.stats.size_bytes -= entry.size_bytes
                    self._current_memory_size -= entry.size_bytes
                    self.stats.entry_count -= 1

                    # Remove from LRU tracking
                    self._lru_order.pop(key, None)

                    # Remove from tag index
                    for tag in entry.tags:
                        if tag in self._tag_index and key in self._tag_index[tag]:
                            self._tag_index[tag].remove(key)
                            if not self._tag_index[tag]:
                                del self._tag_index[tag]

                    del self._entries[key]

                if deleted:
                    self.stats.deletes += 1
                    logger.debug(f"Deleted cache key: {key}")

                return deleted

            except Exception as e:
                logger.error(f"Error deleting cache key {key}: {e}")
                return False

    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key to check

        Returns:
            True if key exists and is not expired
        """
        async with self._lock:
            try:
                # Check memory cache first
                if self._memory_cache and await self._memory_cache.exists(key):
                    return True

                # Check file cache
                entry = self._entries.get(key)
                if entry and not entry.is_expired():
                    return True

                return False

            except Exception as e:
                logger.error(f"Error checking cache key existence {key}: {e}")
                return False

    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            try:
                # Clear memory cache
                if self._memory_cache:
                    await self._memory_cache.clear()

                # Clear file cache
                for cache_file in self._file_cache_path.glob("*"):
                    if cache_file.is_file():
                        cache_file.unlink()

                # Reset tracking
                self._entries.clear()
                self._lru_order.clear()
                self._tag_index.clear()
                self._current_memory_size = 0

                # Reset stats
                old_stats = self.stats
                self.stats = CacheStats()
                self.stats.start_time = old_stats.start_time

                logger.info("Cache cleared")

            except Exception as e:
                logger.error(f"Error clearing cache: {e}")

    async def invalidate_by_tags(self, tags: List[str]) -> int:
        """
        Invalidate all cache entries with specified tags.

        Args:
            tags: List of tags to invalidate

        Returns:
            Number of entries invalidated
        """
        async with self._lock:
            try:
                keys_to_delete = set()

                for tag in tags:
                    if tag in self._tag_index:
                        keys_to_delete.update(self._tag_index[tag])

                # Delete all matching keys
                for key in keys_to_delete:
                    await self.delete(key)

                logger.info(f"Invalidated {len(keys_to_delete)} entries with tags: {tags}")
                return len(keys_to_delete)

            except Exception as e:
                logger.error(f"Error invalidating by tags {tags}: {e}")
                return 0

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        async with self._lock:
            stats_dict = self.stats.to_dict()

            # Add memory usage info
            memory_usage_pct = (
                (self._current_memory_size / self.max_memory_size * 100)
                if self.max_memory_size > 0
                else 0
            )

            stats_dict.update(
                {
                    "memory_cache_size": (
                        len(await self._memory_cache.keys()) if self._memory_cache else 0
                    ),
                    "file_cache_entries": len(self._entries),
                    "tag_count": len(self._tag_index),
                    "expired_entries": sum(
                        1 for entry in self._entries.values() if entry.is_expired()
                    ),
                    "current_memory_size": self._current_memory_size,
                    "max_memory_size": self.max_memory_size,
                    "memory_usage_percent": round(memory_usage_pct, 2),
                    "eviction_policy": self.eviction_policy,
                    "lru_order_size": len(self._lru_order),
                }
            )
            return stats_dict

    async def warm_cache(self, warm_data: Dict[str, Any], ttl: Optional[timedelta] = None) -> int:
        """
        Warm cache with initial data.

        Args:
            warm_data: Dictionary of key-value pairs to pre-populate
            ttl: Optional TTL for warmed data

        Returns:
            Number of entries warmed
        """
        warmed_count = 0

        for key, value in warm_data.items():
            if await self.set(key, value, ttl=ttl, tags=["warmed"]):
                warmed_count += 1

        logger.info(f"Warmed cache with {warmed_count} entries")
        return warmed_count

    async def get_expiring_soon(self, threshold: timedelta = None) -> List[str]:
        """
        Get keys that will expire soon.

        Args:
            threshold: Time threshold for "soon" (default: 5 minutes)

        Returns:
            List of keys expiring soon
        """
        if threshold is None:
            threshold = timedelta(minutes=5)

        expiring_keys = []
        now = datetime.utcnow()

        async with self._lock:
            for key, entry in self._entries.items():
                if entry.expires_at and entry.expires_at - now <= threshold:
                    expiring_keys.append(key)

        return expiring_keys

    async def clear_expired(self) -> int:
        """
        Clear all expired cache entries immediately.

        Returns:
            Number of entries cleared
        """
        async with self._lock:
            try:
                expired_keys = []

                for key, entry in self._entries.items():
                    if entry.is_expired():
                        expired_keys.append(key)

                cleared_count = 0
                for key in expired_keys:
                    if await self.delete(key):
                        cleared_count += 1
                        self.stats.evictions += 1

                logger.info(f"Cleared {cleared_count} expired cache entries")
                return cleared_count

            except Exception as e:
                logger.error(f"Error clearing expired entries: {e}")
                return 0

    async def extend_ttl(self, key: str, additional_time: timedelta) -> bool:
        """
        Extend TTL for a cache entry.

        Args:
            key: Cache key
            additional_time: Additional time to add

        Returns:
            True if TTL was extended
        """
        async with self._lock:
            try:
                if key in self._entries:
                    entry = self._entries[key]
                    if entry.expires_at:
                        entry.expires_at += additional_time
                        await self._set_in_file_cache(key, entry)
                        logger.debug(f"Extended TTL for key {key} by {additional_time}")
                        return True
                return False

            except Exception as e:
                logger.error(f"Error extending TTL for key {key}: {e}")
                return False

    def generate_key(self, data_type: str, **kwargs) -> str:
        """
        Generate intelligent cache keys for different data types.

        Args:
            data_type: Type of data (player, league, matchup, lineup, etc.)
            **kwargs: Key-value pairs to include in the key

        Returns:
            Generated cache key
        """
        try:
            # Sort kwargs for consistent key generation
            sorted_params = sorted(kwargs.items())

            # Create base key components
            key_parts = [f"ff:{data_type}"]

            for key, value in sorted_params:
                if value is not None:
                    # Handle different value types
                    if isinstance(value, (list, tuple)):
                        value_str = "|".join(str(v) for v in sorted(value))
                    elif isinstance(value, dict):
                        # Flatten dict and sort by keys
                        dict_items = sorted(value.items())
                        value_str = "|".join(f"{k}={v}" for k, v in dict_items)
                    else:
                        value_str = str(value)

                    key_parts.append(f"{key}:{value_str}")

            # Join with separator and hash if too long
            full_key = ":".join(key_parts)

            # Hash if key is too long to avoid filesystem issues
            if len(full_key) > 200:
                hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]
                truncated = full_key[:180]
                full_key = f"{truncated}:hash:{hash_suffix}"

            return full_key

        except Exception as e:
            logger.error(f"Error generating cache key for {data_type}: {e}")
            # Fallback to simple hash
            fallback_data = f"{data_type}:{kwargs}"
            return f"ff:fallback:{hashlib.sha256(fallback_data.encode()).hexdigest()[:16]}"

    async def invalidate_by_league(self, league_id: str) -> int:
        """
        Invalidate all cache entries for a specific league.

        Args:
            league_id: League ID to invalidate

        Returns:
            Number of entries invalidated
        """
        pattern = f"ff:*:league_id:{league_id}*"
        return await self._invalidate_by_pattern(pattern)

    async def invalidate_by_week(self, week: int, year: Optional[int] = None) -> int:
        """
        Invalidate all cache entries for a specific week.

        Args:
            week: Week number to invalidate
            year: Optional year (defaults to current year)

        Returns:
            Number of entries invalidated
        """
        if year:
            pattern = f"ff:*:week:{week}:year:{year}*"
        else:
            pattern = f"ff:*:week:{week}*"
        return await self._invalidate_by_pattern(pattern)

    async def invalidate_by_player(self, player_id: str) -> int:
        """
        Invalidate all cache entries for a specific player.

        Args:
            player_id: Player ID to invalidate

        Returns:
            Number of entries invalidated
        """
        pattern = f"ff:*:player_id:{player_id}*"
        return await self._invalidate_by_pattern(pattern)

    async def invalidate_by_team(self, team_id: str) -> int:
        """
        Invalidate all cache entries for a specific team.

        Args:
            team_id: Team ID to invalidate

        Returns:
            Number of entries invalidated
        """
        pattern = f"ff:*:team_id:{team_id}*"
        return await self._invalidate_by_pattern(pattern)

    async def _invalidate_by_pattern(self, pattern: str) -> int:
        """
        Invalidate cache entries matching a pattern.

        Args:
            pattern: Pattern to match (supports * wildcards)

        Returns:
            Number of entries invalidated
        """
        async with self._lock:
            try:
                import fnmatch

                keys_to_delete = []
                for key in self._entries.keys():
                    if fnmatch.fnmatch(key, pattern):
                        keys_to_delete.append(key)

                # Delete all matching keys
                deleted_count = 0
                for key in keys_to_delete:
                    if await self.delete(key):
                        deleted_count += 1

                logger.info(f"Invalidated {deleted_count} entries matching pattern: {pattern}")
                return deleted_count

            except Exception as e:
                logger.error(f"Error invalidating by pattern {pattern}: {e}")
                return 0

    async def _get_from_file_cache(self, key: str) -> Optional[Any]:
        """Get value from file cache."""
        try:
            entry = self._entries.get(key)
            if not entry or entry.is_expired():
                return None

            cache_file = self._file_cache_path / f"{self._hash_key(key)}.pkl"
            if cache_file.exists():
                with open(cache_file, "rb") as f:
                    cached_entry = pickle.load(f)
                    if not cached_entry.is_expired():
                        cached_entry.touch()
                        return cached_entry.value
                    else:
                        # Clean up expired file
                        cache_file.unlink()
                        if key in self._entries:
                            del self._entries[key]

            return None

        except Exception as e:
            logger.error(f"Error reading from file cache for key {key}: {e}")
            return None

    async def _set_in_file_cache(self, key: str, entry: CacheEntry) -> bool:
        """Set value in file cache."""
        try:
            cache_file = self._file_cache_path / f"{self._hash_key(key)}.pkl"
            with open(cache_file, "wb") as f:
                pickle.dump(entry, f)
            return True

        except Exception as e:
            logger.error(f"Error writing to file cache for key {key}: {e}")
            return False

    async def _delete_from_file_cache(self, key: str) -> bool:
        """Delete value from file cache."""
        try:
            cache_file = self._file_cache_path / f"{self._hash_key(key)}.pkl"
            if cache_file.exists():
                cache_file.unlink()
                return True
            return False

        except Exception as e:
            logger.error(f"Error deleting from file cache for key {key}: {e}")
            return False

    async def _load_file_cache_index(self) -> None:
        """Load file cache index from disk."""
        try:
            index_file = self._file_cache_path / "index.json"
            if index_file.exists():
                with open(index_file, "r") as f:
                    index_data = json.load(f)

                # Rebuild entries tracking
                for key_data in index_data.get("entries", []):
                    entry = CacheEntry(
                        key=key_data["key"],
                        value=None,  # Will be loaded on demand
                        created_at=datetime.fromisoformat(key_data["created_at"]),
                        expires_at=(
                            datetime.fromisoformat(key_data["expires_at"])
                            if key_data.get("expires_at")
                            else None
                        ),
                        access_count=key_data.get("access_count", 0),
                        last_accessed=(
                            datetime.fromisoformat(key_data["last_accessed"])
                            if key_data.get("last_accessed")
                            else None
                        ),
                        tags=key_data.get("tags", []),
                        size_bytes=key_data.get("size_bytes", 0),
                    )

                    # Only keep non-expired entries
                    if not entry.is_expired():
                        self._entries[key_data["key"]] = entry
                        await self._update_tag_index(key_data["key"], entry.tags)

                logger.info(f"Loaded {len(self._entries)} cache entries from index")

        except Exception as e:
            logger.error(f"Error loading file cache index: {e}")

    async def _save_file_cache_index(self) -> None:
        """Save file cache index to disk."""
        try:
            index_data = {"entries": []}

            for key, entry in self._entries.items():
                if not entry.is_expired():
                    entry_data = {
                        "key": key,
                        "created_at": entry.created_at.isoformat(),
                        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                        "access_count": entry.access_count,
                        "last_accessed": (
                            entry.last_accessed.isoformat() if entry.last_accessed else None
                        ),
                        "tags": entry.tags,
                        "size_bytes": entry.size_bytes,
                    }
                    index_data["entries"].append(entry_data)

            index_file = self._file_cache_path / "index.json"
            with open(index_file, "w") as f:
                json.dump(index_data, f, indent=2)

            logger.debug(f"Saved cache index with {len(index_data['entries'])} entries")

        except Exception as e:
            logger.error(f"Error saving file cache index: {e}")

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop for expired entries."""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                await self._cleanup_expired()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_expired(self) -> None:
        """Clean up expired cache entries."""
        async with self._lock:
            try:
                expired_keys = []

                for key, entry in self._entries.items():
                    if entry.is_expired():
                        expired_keys.append(key)

                for key in expired_keys:
                    await self.delete(key)
                    self.stats.evictions += 1

                if expired_keys:
                    logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")

            except Exception as e:
                logger.error(f"Error during cleanup: {e}")

    async def _record_hit(self, key: str, level: CacheLevel) -> None:
        """Record cache hit statistics."""
        self.stats.hits += 1

        if key in self._entries:
            self._entries[key].touch()
            # Update LRU order
            if key in self._lru_order:
                self._lru_order.move_to_end(key)

        logger.debug(f"Cache hit for key {key} at level {level}")

    async def _update_tag_index(self, key: str, tags: List[str]) -> None:
        """Update tag index for key."""
        for tag in tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = []
            if key not in self._tag_index[tag]:
                self._tag_index[tag].append(key)

    def _hash_key(self, key: str) -> str:
        """Generate hash for cache key."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _estimate_size(self, value: Any) -> int:
        """Estimate size of value in bytes."""
        try:
            return len(pickle.dumps(value))
        except Exception:
            # Fallback estimation
            if isinstance(value, str):
                return len(value.encode("utf-8"))
            elif isinstance(value, (int, float)):
                return 8
            elif isinstance(value, bool):
                return 1
            elif isinstance(value, (list, dict)):
                return len(str(value))
            else:
                return 100  # Default estimate

    async def _enforce_memory_limits(self, new_entry_size: int = 0) -> None:
        """
        Enforce memory limits by evicting entries if necessary.

        Args:
            new_entry_size: Size of entry being added
        """
        if self.max_memory_size <= 0:
            return

        target_size = self.max_memory_size - new_entry_size

        while self._current_memory_size > target_size and self._lru_order:
            # Evict based on policy
            if self.eviction_policy == EvictionPolicy.LRU:
                # Remove least recently used
                lru_key = next(iter(self._lru_order))
                await self._evict_entry(lru_key)

            elif self.eviction_policy == EvictionPolicy.LFU:
                # Remove least frequently used
                lfu_key = min(self._entries.keys(), key=lambda k: self._entries[k].access_count)
                await self._evict_entry(lfu_key)

            elif self.eviction_policy == EvictionPolicy.SIZE:
                # Remove largest entry
                largest_key = max(self._entries.keys(), key=lambda k: self._entries[k].size_bytes)
                await self._evict_entry(largest_key)

            else:  # TTL-based
                # Remove entry closest to expiration
                closest_expiry_key = min(
                    (k for k, v in self._entries.items() if v.expires_at),
                    key=lambda k: self._entries[k].expires_at,
                    default=None,
                )
                if closest_expiry_key:
                    await self._evict_entry(closest_expiry_key)
                else:
                    # Fallback to LRU if no TTL entries
                    if self._lru_order:
                        lru_key = next(iter(self._lru_order))
                        await self._evict_entry(lru_key)
                    else:
                        break

    async def _evict_entry(self, key: str) -> None:
        """
        Evict a specific cache entry.

        Args:
            key: Cache key to evict
        """
        try:
            if await self.delete(key):
                self.stats.evictions += 1
                logger.debug(f"Evicted cache entry: {key}")
        except Exception as e:
            logger.error(f"Error evicting cache entry {key}: {e}")

    def get_memory_usage(self) -> Dict[str, Any]:
        """
        Get detailed memory usage information.

        Returns:
            Dictionary with memory usage details
        """
        with self._thread_lock:
            usage_pct = (
                (self._current_memory_size / self.max_memory_size * 100)
                if self.max_memory_size > 0
                else 0
            )

            return {
                "current_size_bytes": self._current_memory_size,
                "current_size_mb": round(self._current_memory_size / 1024 / 1024, 2),
                "max_size_bytes": self.max_memory_size,
                "max_size_mb": round(self.max_memory_size / 1024 / 1024, 2),
                "usage_percent": round(usage_pct, 2),
                "entry_count": len(self._entries),
                "avg_entry_size": (
                    round(self._current_memory_size / len(self._entries), 2) if self._entries else 0
                ),
            }
