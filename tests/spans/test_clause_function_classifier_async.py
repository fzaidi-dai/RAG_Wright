"""ASYNC-B1 (ADR-0057): the async batched clause-function classifier. Hermetic -- an injected async runnable,
no real model. Covers the async shape, the graceful degrade (incl. a ModelCallTimeout), and concurrent
sub-batches via asyncio.gather.
"""
from __future__ import annotations

from rag_wright.models.seam import ModelCallTimeout
from rag_wright.packs.contracts.spans.clause_function_classifier import (
    BatchSpanClassification,
    LlmBatchClauseClassifier,
    RawScore,
    SpanFunctions,
)


class _FakeAsyncRunnable:
    def __init__(self, out=None, raises=None):
        self._out = out
        self._raises = raises
        self.calls = 0

    async def ainvoke(self, _prompt):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._out


def _batch(*pairs):  # pairs: (span_index, [(function, confidence), ...])
    return BatchSpanClassification(spans=[
        SpanFunctions(span_index=i, functions=[RawScore(function=f, confidence=c) for f, c in fs])
        for i, fs in pairs])


async def test_aclassify_spans_aligns_by_span_index():
    out = _batch((0, [("Cap On Liability", "high")]), (1, [("Indemnification", "high")]))
    clf = LlmBatchClauseClassifier(_FakeAsyncRunnable(out))
    scores = await clf.aclassify_spans("section context", ["span A", "span B"])
    assert scores[0][0].function == "Cap On Liability"
    assert scores[1][0].function == "Indemnification"


async def test_aclassify_degrades_on_failure_including_timeout():
    clf = LlmBatchClauseClassifier(_FakeAsyncRunnable(raises=ModelCallTimeout("deadline")))
    scores = await clf.aclassify_spans("ctx", ["span A", "span B"])
    assert scores == [[], []]  # a failed/timed-out sub-batch leaves its spans empty (degrade, never crash)


async def test_aclassify_runs_subbatches_concurrently():
    # > _BATCH_CAP spans -> multiple sub-batches, each one ainvoke, run via gather
    fake = _FakeAsyncRunnable(_batch((0, [("Governing Law", "high")])))
    clf = LlmBatchClauseClassifier(fake)
    await clf.aclassify_spans("ctx", [f"span {i}" for i in range(25)])  # 3 sub-batches (10 + 10 + 5)
    assert fake.calls == 3


async def test_aclassify_spans_honors_a_passed_shared_semaphore():
    # CLASSIFY-CONCURRENCY-1: when the caller (segment_and_classify over many chunks) passes ONE shared sem, the
    # classifier's sub-batch calls acquire it -- so total in-flight is bounded by that single deliberate knob.
    import asyncio

    class _ConcurrentRunnable:
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def ainvoke(self, _prompt):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.02)
            self.in_flight -= 1
            return _batch((0, [("Governing Law", "high")]))

    fake = _ConcurrentRunnable()
    clf = LlmBatchClauseClassifier(fake)
    sem = asyncio.Semaphore(1)  # the shared bound
    await clf.aclassify_spans("ctx", [f"span {i}" for i in range(25)], sem=sem)  # 3 sub-batches
    assert fake.max_in_flight == 1  # the passed sem serialized the sub-batch calls (a single deliberate knob)
