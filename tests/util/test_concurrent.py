"""Tests for the bounded-concurrent map + flushed progress file (util/concurrent.py). Hermetic."""

from __future__ import annotations

import threading

from rag_wright.util.concurrent import Progress, map_concurrent


def test_map_concurrent_preserves_order_and_runs_all():
    out = map_concurrent([1, 2, 3, 4, 5], lambda x: x * 10, max_concurrency=3)
    assert out == [10, 20, 30, 40, 50]  # results in INPUT order despite concurrency


def test_map_concurrent_actually_overlaps_within_the_bound():
    # a barrier of size 4 only releases if >=4 calls are in flight at once -> proves real concurrency
    # AND that the semaphore bound is honored (max_concurrency=4, barrier=4).
    barrier = threading.Barrier(4, timeout=5)

    def fn(_x: int) -> int:
        return barrier.wait()  # blocks until 4 threads arrive; raises BrokenBarrierError on timeout

    out = map_concurrent(range(8), fn, max_concurrency=4)
    # each barrier batch of 4 returns arrival indices 0..3; two batches -> each index appears twice
    assert sorted(out) == [0, 0, 1, 1, 2, 2, 3, 3]


def test_progress_file_reaches_total(tmp_path):
    path = tmp_path / "progress.log"
    map_concurrent(range(20), lambda x: x, max_concurrency=5, progress_path=path, label="confirm", every=5)
    content = path.read_text(encoding="utf-8")
    assert "confirm 20/20 (100%)" in content


def test_progress_counts_without_a_path():
    p = Progress(3)  # no path -> pure counter, no I/O
    for _ in range(3):
        p.tick()
    assert p.done == 3


def test_progress_echoes_x_over_n_to_stdout(capsys):
    # echo=True prints X/N to stdout (progress "in front", not just a file)
    map_concurrent(range(4), lambda x: x, max_concurrency=2, label="[job]", echo=True, every=1)
    out = capsys.readouterr().out
    assert "[job] 4/4 (100%)" in out and "[job] 1/4" in out


def test_timeout_bounds_a_stalled_call_and_does_not_hang():
    import time as _t
    from rag_wright.util.concurrent import map_concurrent

    def maybe_slow(x):
        if x == 1:
            _t.sleep(1.5)  # stalls past the 0.2s deadline (simulates a hung LLM read)
        return x * 10

    start = _t.perf_counter()
    out = map_concurrent([0, 1, 2], maybe_slow, max_concurrency=3, timeout_s=0.2, timeout_retries=0)
    elapsed = _t.perf_counter() - start
    assert elapsed < 1.2  # bounded: did NOT wait for the 1.5s task
    assert out == [0, None, 20]  # the stalled task -> None, order preserved, others unaffected


def test_no_timeout_preserves_default_behavior():
    from rag_wright.util.concurrent import map_concurrent
    assert map_concurrent([1, 2, 3], lambda x: x + 1) == [2, 3, 4]
