"""Bounded concurrent execution with live progress (the async+semaphore pattern, packaged once).

CLAUDE.md standing rule: any eval / script / agentic loop that fires more than one model/LLM call must
drive it concurrently (async + `Semaphore` + `to_thread` + `gather`), never sequentially -- same tokens,
far less wall-clock. This packages that idiom (as in `embed_chunks`) once, and adds a FLUSHED progress
file so a long run is observable (done/total, rate, ETA) instead of a black box behind a provider
dashboard. Reused by the T60 label bootstrap and the T57b property extractor (both fan out LLM calls).

`fn` is a plain SYNC callable (an LLM `.invoke`, an encoder, etc.); it runs in a worker thread so many
calls are in flight at once, bounded by `max_concurrency`. Results come back in input order.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_MAX_CONCURRENCY = 8


class Progress:
    """Counts completions and writes ``<label> done/total (pct)  elapsed  rate  eta`` to a FLUSHED file
    so a caller can tail it (stdout through a pipe is often block-buffered). Ticks are serialized on the
    event loop, so the counter needs no lock. With no `path`, it just counts (no I/O)."""

    def __init__(
        self, total: int, *, path: Optional[Path] = None, label: str = "", every: int = 1, echo: bool = False
    ) -> None:
        self.total = total
        self.done = 0
        self._path = Path(path) if path else None
        self._label = label
        self._every = max(1, every)
        self._echo = echo  # also print X/N to stdout (flushed) -- progress "in front", not just in a file
        self._t0 = time.perf_counter()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path is not None or self._echo:
            self._write()

    def tick(self) -> None:
        self.done += 1
        if self.done % self._every == 0 or self.done == self.total:
            self._write()

    def _write(self) -> None:
        elapsed = time.perf_counter() - self._t0
        rate = self.done / elapsed if elapsed > 0 and self.done else 0.0
        pct = 100.0 * self.done / self.total if self.total else 100.0
        line = f"{self._label} {self.done}/{self.total} ({pct:.0f}%)  elapsed={elapsed:.0f}s  rate={rate:.1f}/s"
        if rate > 0 and self.done < self.total:
            line += f"  eta={(self.total - self.done) / rate:.0f}s"
        if self._path is not None:
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        if self._echo:
            print(line, flush=True)


async def map_concurrent_async(
    items: Iterable[T],
    fn: Callable[[T], R],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    progress: Optional[Progress] = None,
) -> list[R]:
    """Run sync `fn` over each item concurrently in a worker thread, bounded by a semaphore; results in
    input order. Increments `progress` as each call completes (for the flushed progress file)."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _one(item: T) -> R:
        async with semaphore:  # backpressure
            result = await asyncio.to_thread(fn, item)
        if progress is not None:
            progress.tick()  # serialized on the event loop -> no lock
        return result

    return list(await asyncio.gather(*(_one(item) for item in items)))


def map_concurrent(
    items: Iterable[T],
    fn: Callable[[T], R],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    progress_path: Optional[Path] = None,
    label: str = "",
    every: int = 1,
    echo: bool = False,
) -> list[R]:
    """Synchronous convenience for callers not already in an event loop: bounded-concurrent map with a
    flushed progress file and/or a stdout echo (`done/total`, rate, ETA), so progress is visible live."""
    items = list(items)
    progress = (
        Progress(len(items), path=progress_path, label=label, every=every, echo=echo)
        if (progress_path or echo)
        else None
    )
    return asyncio.run(map_concurrent_async(items, fn, max_concurrency=max_concurrency, progress=progress))
