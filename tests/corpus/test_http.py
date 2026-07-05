"""Tests for the throttled, disk-cached fetcher (T7, guardrail 1).

EDGAR rate-limits and rejects requests without a User-Agent; an IP block would stall the task. So
the fetcher must be a real aggregate rate limiter (not a per-call sleep) and a durable on-disk cache
(a fetched URL is never re-fetched across runs). The transport is injectable so this is tested with
no network.
"""

from rag_wright.corpus.http import DiskCache, RateLimiter, ThrottledCachingFetcher


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, dt):
        self.sleeps.append(dt)
        self.t += dt


# --- RateLimiter: real aggregate limiter over a rolling 1s window ---------------------------


def test_rate_limiter_allows_a_full_burst_up_to_the_cap_without_sleeping():
    clock = _FakeClock()
    limiter = RateLimiter(max_per_sec=10, clock=clock.now, sleep=clock.sleep)
    for _ in range(10):
        limiter.acquire()
    assert clock.sleeps == []  # 10 in the first window is allowed


def test_rate_limiter_blocks_the_over_cap_call_until_the_window_frees():
    clock = _FakeClock()
    limiter = RateLimiter(max_per_sec=10, clock=clock.now, sleep=clock.sleep)
    for _ in range(10):
        limiter.acquire()  # all at t=0
    limiter.acquire()  # 11th must wait ~1s for the oldest to exit the window
    assert clock.sleeps and abs(clock.sleeps[0] - 1.0) < 1e-9


def test_rate_limiter_aggregate_never_exceeds_cap_in_any_window():
    clock = _FakeClock()
    limiter = RateLimiter(max_per_sec=5, clock=clock.now, sleep=clock.sleep)
    times = []
    for _ in range(20):
        limiter.acquire()
        times.append(clock.now())
    # No 1-second window contains more than 5 acquisitions.
    for i in range(len(times)):
        in_window = [t for t in times if times[i] <= t < times[i] + 1.0]
        assert len(in_window) <= 5


# --- DiskCache: durable across runs ---------------------------------------------------------


def test_disk_cache_put_then_get(tmp_path):
    cache = DiskCache(tmp_path)
    assert cache.get("http://x/a") is None
    cache.put("http://x/a", b"payload")
    assert cache.get("http://x/a") == b"payload"


def test_disk_cache_is_durable_across_instances(tmp_path):
    DiskCache(tmp_path).put("http://x/b", b"kept")
    # A fresh cache over the same root (a later run) still has it.
    assert DiskCache(tmp_path).get("http://x/b") == b"kept"


# --- ThrottledCachingFetcher: cache short-circuits the network + limiter ---------------------


def _fetcher(tmp_path, transport, *, max_per_sec=10):
    clock = _FakeClock()
    return ThrottledCachingFetcher(
        user_agent="RAG_Wright research contact@example.com",
        cache=DiskCache(tmp_path),
        limiter=RateLimiter(max_per_sec=max_per_sec, clock=clock.now, sleep=clock.sleep),
        transport=transport,
    )


def test_fetch_miss_calls_transport_with_user_agent_then_caches(tmp_path):
    seen = {}

    def transport(url, headers):
        seen["url"] = url
        seen["ua"] = headers.get("User-Agent")
        return b"body"

    fetcher = _fetcher(tmp_path, transport)
    assert fetcher.get("http://x/c") == b"body"
    assert seen["url"] == "http://x/c"
    assert "RAG_Wright" in seen["ua"]
    assert DiskCache(tmp_path).get("http://x/c") == b"body"  # cached


def test_fetch_hit_does_not_touch_the_network(tmp_path):
    calls = {"n": 0}

    def transport(url, headers):
        calls["n"] += 1
        return b"once"

    fetcher = _fetcher(tmp_path, transport)
    fetcher.get("http://x/d")  # miss -> transport
    fetcher.get("http://x/d")  # hit -> no transport
    assert calls["n"] == 1


def test_cache_survives_across_fetcher_instances(tmp_path):
    def transport(url, headers):
        raise AssertionError("should not fetch a cached url")

    DiskCache(tmp_path).put("http://x/e", b"warm")
    fetcher = _fetcher(tmp_path, transport)
    assert fetcher.get("http://x/e") == b"warm"  # served from durable cache, transport never called
