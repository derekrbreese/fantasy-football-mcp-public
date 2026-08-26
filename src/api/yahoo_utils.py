#!/usr/bin/env python3
"""
Yahoo API utilities for rate limiting and caching
"""

import asyncio
import time
import hashlib
import json
from typing import Any, Dict, Optional, Callable
from functools import wraps
from collections import deque
from dataclasses import dataclass
from datetime import datetime


class RateLimiter:
    """Rate limiter for Yahoo API calls (1000 requests per hour)."""

    def __init__(self, max_requests: int = 900, window_seconds: int = 3600):
        """Initialize rate limiter with a safety margin below Yahoo's limit."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait if necessary to respect rate limits."""
        async with self._lock:
            now = time.time()
            while self.requests and self.requests[0] <= now - self.window_seconds:
                self.requests.popleft()

            if len(self.requests) >= self.max_requests:
                oldest_request = self.requests[0]
                wait_time = (oldest_request + self.window_seconds) - now
                if wait_time > 0:
                    print(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
                    await asyncio.sleep(wait_time)
                    now = time.time()
                    while self.requests and self.requests[0] <= now - self.window_seconds:
                        self.requests.popleft()

            self.requests.append(now)

    def get_status(self) -> Dict[str, Any]:
        """Get current rate limiter status."""
        now = time.time()
        while self.requests and self.requests[0] <= now - self.window_seconds:
            self.requests.popleft()

        requests_in_window = len(self.requests)
        remaining = self.max_requests - requests_in_window
        reset_time = None
        if self.requests:
            reset_time = self.requests[0] + self.window_seconds
            reset_in_seconds = max(0, reset_time - now)
        else:
            reset_in_seconds = 0

        return {
            "requests_used": requests_in_window,
            "requests_remaining": remaining,
            "max_requests": self.max_requests,
            "reset_in_seconds": round(reset_in_seconds),
            "reset_time": datetime.fromtimestamp(reset_time).isoformat() if reset_time else None,
        }


@dataclass
class CacheEntry:
    data: Any
    timestamp: float
    endpoint: str
    ttl: int

    @property
    def age(self) -> float:
        return time.time() - self.timestamp


class ResponseCache:
    """Simple TTL-based cache for API responses."""

    def __init__(self):
        self.cache: Dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self.default_ttls = {
            "leagues": 3600,
            "teams": 1800,
            "standings": 300,
            "roster": 300,
            "matchup": 60,
            "players": 600,
            "draft": 86400,
            "waiver": 300,
            "user": 3600,
        }

    def _get_cache_key(self, endpoint: str) -> str:
        """Generate cache key from endpoint."""
        return hashlib.md5(endpoint.encode()).hexdigest()

    def ttl_for_endpoint(self, endpoint: str) -> int:
        """Determine TTL from the actual Yahoo endpoint, not a namespaced cache key."""
        if "leagues" in endpoint or "games" in endpoint:
            return self.default_ttls["leagues"]
        if "standings" in endpoint:
            return self.default_ttls["standings"]
        if "roster" in endpoint:
            return self.default_ttls["roster"]
        if "matchup" in endpoint or "scoreboard" in endpoint:
            return self.default_ttls["matchup"]
        if "players" in endpoint and "status=A" in endpoint:
            return self.default_ttls["players"]
        if "draft" in endpoint:
            return self.default_ttls["draft"]
        if "teams" in endpoint:
            return self.default_ttls["teams"]
        if "users" in endpoint:
            return self.default_ttls["user"]
        return 300

    def _get_ttl_for_endpoint(self, endpoint: str) -> int:
        """Backward-compatible alias for older callers/tests."""
        return self.ttl_for_endpoint(endpoint)

    async def get(self, endpoint: str) -> Optional[Any]:
        """Get cached response if valid."""
        cache_key = self._get_cache_key(endpoint)
        async with self._lock:
            entry = self.cache.get(cache_key)
            if entry is None:
                return None

            if not isinstance(entry, CacheEntry):
                data, timestamp = entry
                ttl = self.ttl_for_endpoint(endpoint)
                if time.time() - timestamp < ttl:
                    return data
                del self.cache[cache_key]
                return None

            if entry.age < entry.ttl:
                return entry.data

            del self.cache[cache_key]
            return None

    async def set(self, endpoint: str, data: Any, ttl: Optional[int] = None):
        """Store response in cache."""
        cache_key = self._get_cache_key(endpoint)
        ttl_value = ttl if ttl is not None else self.ttl_for_endpoint(endpoint)
        async with self._lock:
            self.cache[cache_key] = CacheEntry(
                data=data,
                timestamp=time.time(),
                endpoint=endpoint,
                ttl=ttl_value,
            )

    async def clear(self, pattern: Optional[str] = None):
        """Clear cache entries matching pattern or all if no pattern."""
        async with self._lock:
            if pattern:
                keys_to_delete = [
                    key
                    for key, entry in self.cache.items()
                    if pattern in key
                    or (isinstance(entry, CacheEntry) and pattern in entry.endpoint)
                ]
                for key in keys_to_delete:
                    del self.cache[key]
            else:
                self.cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = time.time()
        entries = list(self.cache.values())
        total_entries = len(entries)
        expired_count = 0
        total_size = 0
        sample_endpoints = []
        oldest_age = 0.0

        for entry in entries:
            if isinstance(entry, CacheEntry):
                ttl = entry.ttl
                endpoint = entry.endpoint
                timestamp = entry.timestamp
                data = entry.data
                sample_endpoints.append(endpoint)
            else:
                data, timestamp = entry
                endpoint = "unknown"
                ttl = self.ttl_for_endpoint(endpoint)

            if now - timestamp >= ttl:
                expired_count += 1

            age = now - timestamp
            if age > oldest_age:
                oldest_age = age

            try:
                total_size += len(json.dumps(data, default=str))
            except TypeError:
                pass

        return {
            "total_entries": total_entries,
            "expired_entries": expired_count,
            "active_entries": total_entries - expired_count,
            "cache_size_bytes_est": total_size,
            "cache_size_mb_est": round(total_size / (1024 * 1024), 2),
            "oldest_entry_age_seconds": round(oldest_age, 1),
            "sample_endpoints": sample_endpoints[:5],
        }


rate_limiter = RateLimiter()
response_cache = ResponseCache()


def with_rate_limit(func: Callable) -> Callable:
    """Decorator to add rate limiting to async functions."""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        await rate_limiter.acquire()
        return await func(*args, **kwargs)

    return wrapper


def with_cache(ttl_seconds: Optional[int] = None) -> Callable:
    """Decorator to add caching to async functions."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(endpoint: str, *args, **kwargs):
            cached_response = await response_cache.get(endpoint)
            if cached_response is not None:
                return cached_response

            result = await func(endpoint, *args, **kwargs)
            if result:
                await response_cache.set(endpoint, result, ttl=ttl_seconds)
            return result

        return wrapper

    return decorator
