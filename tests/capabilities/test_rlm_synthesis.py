"""RLM synthesis (T28, FR-Q.5): apply the RLM method to the candidate chunks.

Hermetic tests prove chunks load as data and are sliced per-chunk, that no sub-call ever sees the full
volume (each slice call gets one chunk; each combine gets <= fanout notes), that the reduce recurses,
and that dispatch is concurrent and bounded. The live `-m model` test runs the real DeepSeek sub-calls
concurrently.
"""

from __future__ import annotations

import threading
import time

import pytest

from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.capabilities.rlm_synthesis import (
    SynthesisChunk,
    SynthesisResult,
    register_rlm_synthesis,
    rlm_synthesize,
)


class _StubSynth:
    def synthesize_slice(self, query: str, text: str) -> str:
        return f"note({text})"

    def combine(self, query: str, extracts: list[str]) -> str:
        return "combined[" + "|".join(extracts) + "]"


class _Probe:
    """Records per-call inputs (to prove never-full-volume) and peak concurrency of the slice calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.slice_texts: list[str] = []
        self.combine_sizes: list[int] = []

    def synthesize_slice(self, query: str, text: str) -> str:
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.slice_texts.append(text)
        time.sleep(0.02)  # stand in for a network sub-call so slices overlap
        with self._lock:
            self.inflight -= 1
        return f"n:{text}"

    def combine(self, query: str, extracts: list[str]) -> str:
        with self._lock:
            self.combine_sizes.append(len(extracts))
        return "c:" + ";".join(extracts)


def _chunks(n: int) -> list[SynthesisChunk]:
    return [SynthesisChunk(chunk_id=f"c{i}", text=f"t{i}") for i in range(n)]


# --- the method: load, slice per chunk, synthesize -----------------------------------------------


def test_slices_per_chunk_and_synthesizes():
    chunks = [SynthesisChunk(chunk_id="c1", text="alpha"), SynthesisChunk(chunk_id="c2", text="beta")]
    result = rlm_synthesize("q", chunks, synthesizer=_StubSynth(), fanout=8)

    assert isinstance(result, SynthesisResult)
    assert [s.chunk_id for s in result.slice_outputs] == ["c1", "c2"]  # one slice per chunk
    assert result.slice_outputs[0].extract == "note(alpha)"
    assert result.synthesis.startswith("combined[")  # combined in code from the slice outputs
    assert result.chunk_ids == ["c1", "c2"]  # cited provenance


def test_never_attends_over_the_full_volume():
    probe = _Probe()
    rlm_synthesize("q", _chunks(5), synthesizer=probe, max_concurrency=4, fanout=2)

    assert set(probe.slice_texts) == {"t0", "t1", "t2", "t3", "t4"}  # each chunk sliced once, alone
    assert max(probe.combine_sizes) <= 2  # no combine call ever sees more than `fanout` notes
    assert len(probe.combine_sizes) > 3  # the reduce recursed (multi-level fan-in, not one big call)


def test_dispatch_is_concurrent_and_bounded():
    probe = _Probe()
    rlm_synthesize("q", _chunks(12), synthesizer=probe, max_concurrency=4, fanout=8)
    assert probe.max_inflight == 4  # slices run concurrently, bounded exactly by the semaphore


def test_serial_reaches_only_one_in_flight():
    probe = _Probe()
    rlm_synthesize("q", _chunks(4), synthesizer=probe, max_concurrency=1, fanout=8)
    assert probe.max_inflight == 1


def test_empty_candidate_set_synthesizes_to_empty():
    result = rlm_synthesize("q", [], synthesizer=_StubSynth())
    assert result.slice_outputs == [] and result.synthesis == "" and result.chunk_ids == []


def test_registers_as_an_agent_skill_under_fr_q_5():
    registry = CapabilityRegistry()
    register_rlm_synthesis(registry)
    reg = registry.get("rlm_synthesis")
    assert reg.name == "rlm_synthesis"
    assert reg.kind == "agent_skill"  # applies the RLM method (requires rlm_method)
    assert reg.contract is SynthesisResult


# --- live DeepSeek (opt-in): real concurrent sub-calls -------------------------------------------


@pytest.mark.model
def test_live_rlm_synthesis_over_real_chunks_runs_concurrently():
    from rag_wright.capabilities.rlm_synthesis import SeamSynthesizer

    chunks = [
        SynthesisChunk(chunk_id=f"docA:{i}:h", text=text)
        for i, text in enumerate([
            "This Agreement is between Acme Corporation and Beta LLC, effective January 1, 2020.",
            "This Agreement is governed by the laws of the State of Delaware.",
            "Acme grants Beta an exclusive license to distribute the Products in the Territory.",
        ])
    ]
    result = rlm_synthesize("Who are the parties and what license is granted?", chunks,
                            synthesizer=SeamSynthesizer(), max_concurrency=8)

    assert len(result.slice_outputs) == 3
    assert all(s.extract.strip() for s in result.slice_outputs)  # real per-slice extracts
    assert result.synthesis.strip()  # a combined synthesis
