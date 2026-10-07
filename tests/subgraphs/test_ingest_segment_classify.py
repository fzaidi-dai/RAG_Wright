"""INGEST-LLM-CLASSIFIER (ADR-0048) / ASYNC-B2e (ADR-0057), ported to ING-4c (ADR-0124): the reference pack's
`function_span_tagger` run by the shared segment stage -- ONE classify call per chunk (chunk as context), the PRIMARY
per span (`NONE` when untagged), the multi-label scores carried, chunks classified CONCURRENTLY and bounded by the
shared semaphore. Hermetic: a fake classifier and a text-only document, no LLM."""
from __future__ import annotations

import asyncio

from rag_wright.api import Span, source_document
from rag_wright.capabilities.rlm_chunking import Chunk
from rag_wright.packs.contracts.schemas.function import NO_FUNCTION, FunctionConfidence, FunctionScore
from rag_wright.ingestion.builder import IngestionStages
from rag_wright.packs.contracts.subgraphs.contract_ingestion_pipeline import function_span_tagger


def _fs(function, conf=FunctionConfidence.HIGH):
    return FunctionScore(function=function, confidence=conf)


def _chunks(texts):
    out, start = [], 0
    for i, text in enumerate(texts):
        out.append(Chunk(chunk_id=f"d:{i}:h", chunk_index=i, text=text, summary="", token_estimate=1,
                         doc_start=start, doc_end=start + len(text)))
        start += len(text) + 2
    return out


def _sentence_spans(chunk_id, text, layout):  # tile the chunk text at '. '
    cuts = [0] + [i + 2 for i in range(len(text) - 1) if text[i:i + 2] == ". "] + [len(text)]
    return [Span(span_id=f"{chunk_id}#{k}", parent_chunk_id=chunk_id, span_index=k, start=a, end=b, text=text[a:b])
            for k, (a, b) in enumerate(zip(cuts, cuts[1:]))]


def _segment(tmp_path, texts, clf, *, max_concurrency=8):
    scores = {}
    stages = IngestionStages(None, extractor=None, segmenter=_sentence_spans, cache_dir=tmp_path,
                             span_tagger=function_span_tagger(clf, max_concurrency=max_concurrency, span_scores=scores))
    sd = source_document("d", text="\n\n".join(texts))
    return asyncio.run(stages.segment(sd, _chunks(texts))), scores


def test_one_call_per_chunk_with_the_primary_and_the_scores(tmp_path):
    calls = []

    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            calls.append((chunk_text, tuple(span_texts)))
            if chunk_text.startswith("Cap"):
                return [[_fs("Cap On Liability"), _fs("Indemnification", FunctionConfidence.MEDIUM)],
                        [_fs("Indemnification")]]
            return [[_fs("Governing Law")]]

    tagged, scores = _segment(tmp_path, ["Cap is 12 months. Supplier indemnifies.", "Laws of Delaware."], _Clf())
    assert len(calls) == 2 and calls[0] == ("Cap is 12 months. Supplier indemnifies.",
                                            ("Cap is 12 months. ", "Supplier indemnifies."))
    assert [t.primary_tag for t in tagged] == ["Cap On Liability", "Indemnification", "Governing Law"]
    assert tagged[0].tags == ["Cap On Liability", "Indemnification"]                    # multi-label carried
    assert [f.function for f in scores[tagged[0].span.span_id]] == ["Cap On Liability", "Indemnification"]


def test_an_untagged_span_is_none_with_no_scores(tmp_path):
    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            return [[], [_fs("Cap On Liability")]]

    tagged, _ = _segment(tmp_path, ["Recitals apply. Cap is 12 months."], _Clf())
    assert [t.primary_tag for t in tagged] == [NO_FUNCTION, "Cap On Liability"] and tagged[0].tags == []


def test_chunks_are_classified_concurrently_and_in_order(tmp_path):
    class _Clf:
        def __init__(self):
            self.in_flight = self.max_in_flight = 0

        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.02)
            self.in_flight -= 1
            return [[_fs("Governing Law")] for _ in span_texts]

    clf = _Clf()
    tagged, _ = _segment(tmp_path, [f"Span number {i}." for i in range(4)], clf)
    assert clf.max_in_flight > 1                                                    # concurrent across chunks
    assert [t.span.text for t in tagged] == [f"Span number {i}." for i in range(4)]  # order preserved


def test_classify_concurrency_is_bounded_by_the_shared_semaphore(tmp_path):
    class _Clf:
        def __init__(self):
            self.in_flight = self.max_in_flight = 0

        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            async with sem:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                await asyncio.sleep(0.02)
                self.in_flight -= 1
            return [[_fs("Governing Law")] for _ in span_texts]

    clf = _Clf()
    _segment(tmp_path, [f"Span number {i}." for i in range(6)], clf, max_concurrency=2)
    assert clf.max_in_flight == 2


def test_an_unset_classify_concurrency_falls_back_to_the_env_knob(tmp_path, monkeypatch):
    # ING-4c regression: the production ingest passes `classify_concurrency=None` when no override is configured;
    # the tagger must then use CLASSIFY_CONCURRENCY (default 8), as the pre-port segment leaf did -- not crash.
    monkeypatch.setenv("CLASSIFY_CONCURRENCY", "2")
    seen = []

    class _Clf:
        async def aclassify_spans(self, chunk_text, span_texts, *, sem=None):
            seen.append(sem._value)
            return [[_fs("Governing Law")] for _ in span_texts]

    tagged, _ = _segment(tmp_path, ["Law is Delaware."], _Clf(), max_concurrency=None)
    assert [t.primary_tag for t in tagged] == ["Governing Law"] and seen == [2]  # the env knob sized it
