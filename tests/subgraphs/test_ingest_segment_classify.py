"""INGEST-LLM-CLASSIFIER (ADR-0048) / ASYNC-B2e (ADR-0057): `_asegment_and_classify` -- batched per-chunk
classification (option B) on the async classifier. Hermetic: injected segmenter + async classifier, no LLM.
Asserts ONE classify call per chunk (chunk as context), the PRIMARY per span, NONE for no-function spans, and
the multi-label scores carried through."""

from __future__ import annotations

from rag_wright.contracts.function import NO_FUNCTION, FunctionConfidence, FunctionScore
from rag_wright.subgraphs.contract_ingestion_pipeline import _asegment_and_classify


class _Op:
    def __init__(self, text):
        self.text = text
        self.span_id = f"sid-{text}"


class _Chunk:
    def __init__(self, chunk_id, text, doc_start):
        self.chunk_id = chunk_id
        self.text = text
        self.doc_start = doc_start


def _fs(function, conf=FunctionConfidence.HIGH):
    return FunctionScore(function=function, confidence=conf)


async def test_batches_one_call_per_chunk_and_assigns_primary_with_scores():
    seg = {"c1": [_Op("cap"), _Op("indem")], "c2": [_Op("gov")]}
    calls = []

    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            calls.append((chunk_text, tuple(span_texts)))
            if chunk_text == "chunk-1":
                return [[_fs("Cap On Liability"), _fs("Indemnification", FunctionConfidence.MEDIUM)],
                        [_fs("Indemnification")]]
            return [[_fs("Governing Law")]]

    chunks = [_Chunk("c1", "chunk-1", 0), _Chunk("c2", "chunk-2", 100)]
    segs = await _asegment_and_classify(chunks, _Clf(), segment=lambda cid, text: seg[cid])

    assert len(calls) == 2                                   # ONE classify call per chunk (batched)
    assert calls[0] == ("chunk-1", ("cap", "indem"))       # both spans of chunk-1 passed together, chunk as context
    assert [s[1] for s in segs] == ["Cap On Liability", "Indemnification", "Governing Law"]  # per-span primary
    assert [s[2] for s in segs] == [0, 0, 100]              # chunk_doc_start carried
    assert [f.function for f in segs[0][3]] == ["Cap On Liability", "Indemnification"]  # multi-label scores carried


async def test_no_function_span_becomes_none_sentinel():
    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            return [[], [_fs("Cap On Liability")]]  # first span -> no function

    chunks = [_Chunk("c", "ctx", 0)]
    segs = await _asegment_and_classify(chunks, _Clf(), segment=lambda cid, text: [_Op("a"), _Op("b")])
    assert [s[1] for s in segs] == [NO_FUNCTION, "Cap On Liability"]
    assert segs[0][3] == []


async def test_empty_chunk_is_skipped():
    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            raise AssertionError("should not classify an empty chunk")

    segs = await _asegment_and_classify([_Chunk("c", "ctx", 0)], _Clf(), segment=lambda cid, text: [])
    assert segs == []


async def test_chunks_are_classified_concurrently_not_sequentially():
    # CLASSIFY-CONCURRENCY-1: the chunks must classify CONCURRENTLY (was one-after-another). A stub that tracks
    # in-flight aclassify_spans calls should see >1 at once; the sequential version could only ever reach 1.
    import asyncio

    seg = {f"c{i}": [_Op(f"span-{i}")] for i in range(4)}

    class _Clf:
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.02)  # hold the "call" open so concurrent chunks overlap
            self.in_flight -= 1
            return [[_fs("Governing Law")] for _ in span_texts]

    clf = _Clf()
    chunks = [_Chunk(f"c{i}", f"chunk-{i}", i * 100) for i in range(4)]
    segs = await _asegment_and_classify(chunks, clf, segment=lambda cid, text: seg[cid])

    assert clf.max_in_flight > 1                        # concurrent across chunks (sequential would be exactly 1)
    assert len(segs) == 4                               # every span classified, output order preserved
    assert [op.text for op, *_ in segs] == [f"span-{i}" for i in range(4)]


async def test_classify_concurrency_is_bounded_by_the_shared_semaphore():
    # the single deliberate knob: with max_concurrency=2, at most 2 chunk classifications are in flight at once.
    import asyncio

    seg = {f"c{i}": [_Op(f"s{i}")] for i in range(6)}

    class _Clf:
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            async with (sem or asyncio.Semaphore(99)):   # honor the shared bound, as the real classifier does
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                await asyncio.sleep(0.02)
                self.in_flight -= 1
            return [[_fs("Governing Law")] for _ in span_texts]

    clf = _Clf()
    chunks = [_Chunk(f"c{i}", f"chunk-{i}", i * 100) for i in range(6)]
    await _asegment_and_classify(chunks, clf, segment=lambda cid, text: seg[cid], max_concurrency=2)
    assert clf.max_in_flight == 2                        # bounded by the shared semaphore (the deliberate knob)
