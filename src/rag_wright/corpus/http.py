"""Throttled, disk-cached HTTP fetching for corpus acquisition (T7, guardrail 1).

EDGAR rejects requests without a User-Agent and rate-limits; an IP block would stall the task. So
every fetch goes through a fetcher that (1) enforces a real aggregate rate limit over a rolling
one-second window (not a per-call sleep, so a burst never exceeds the cap) and (2) reads through a
durable on-disk cache, so a URL fetched in one run is never re-fetched in a later run.

The transport (the actual GET) is injected, so the throttle and cache logic are testable without
network. The CLI (`scripts/acquire_edgar.py`) supplies a real `requests`-backed transport.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Optional

Transport = Callable[[str, dict[str, str]], bytes]


class RateLimiter:
    """An aggregate rate limiter: at most `max_per_sec` acquisitions in any rolling 1-second window.

    Tracks the timestamps of recent acquisitions; when the window is full, it sleeps exactly until
    the oldest one leaves the window. This bounds the aggregate rate, unlike a fixed per-call sleep.
    """

    def __init__(
        self,
        max_per_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        window: float = 1.0,
    ) -> None:
        if max_per_sec <= 0:
            raise ValueError("max_per_sec must be positive")
        self._max = max_per_sec
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._events: deque[float] = deque()

    def _evict(self, now: float) -> None:
        while self._events and now - self._events[0] >= self._window:
            self._events.popleft()

    def acquire(self) -> None:
        now = self._clock()
        self._evict(now)
        if len(self._events) >= self._max:
            wait = self._window - (now - self._events[0])
            if wait > 0:
                self._sleep(wait)
            now = self._clock()
            self._evict(now)
        self._events.append(now)


class DiskCache:
    """A durable key->bytes cache backed by files under `root` (survives across runs)."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".cache")

    def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put(self, key: str, value: bytes) -> None:
        self._path(key).write_bytes(value)


class ThrottledCachingFetcher:
    """Fetch a URL through the durable cache and the aggregate rate limiter.

    A cache hit short-circuits both the network and the limiter; only a miss consumes rate budget
    and calls the transport, then caches the result.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        cache: DiskCache,
        limiter: RateLimiter,
        transport: Transport,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("a non-empty User-Agent is required (EDGAR rejects requests without one)")
        self._ua = user_agent
        self._cache = cache
        self._limiter = limiter
        self._transport = transport

    def get(self, url: str) -> bytes:
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        self._limiter.acquire()
        data = self._transport(url, {"User-Agent": self._ua})
        self._cache.put(url, data)
        return data
