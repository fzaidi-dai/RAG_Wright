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
        async def aclassify_spans(self, chunk_text, span_texts):
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
        async def aclassify_spans(self, chunk_text, span_texts):
            return [[], [_fs("Cap On Liability")]]  # first span -> no function

    chunks = [_Chunk("c", "ctx", 0)]
    segs = await _asegment_and_classify(chunks, _Clf(), segment=lambda cid, text: [_Op("a"), _Op("b")])
    assert [s[1] for s in segs] == [NO_FUNCTION, "Cap On Liability"]
    assert segs[0][3] == []


async def test_empty_chunk_is_skipped():
    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts):
            raise AssertionError("should not classify an empty chunk")

    segs = await _asegment_and_classify([_Chunk("c", "ctx", 0)], _Clf(), segment=lambda cid, text: [])
    assert segs == []
