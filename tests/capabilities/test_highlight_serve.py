"""CU-C2: serve + citation. Tests the four routing branches (highlight / extract / discriminate-stub /
out-of-taxonomy fallback) and citation assembly hermetically -- fake store, embedder, and extractor factory;
no LLM, no network, no store."""

from __future__ import annotations

from rag_wright.packs.contracts.capabilities.highlight_serve import _Extracted, serve_highlight
from rag_wright.packs.contracts.schemas.query_intent import QueryIntent


def _row(span_id, function, text, doc_start, doc_end, dense=None):
    return {"span_id": span_id, "document_id": "C1", "primary_tag": function, "parent_chunk_id": "C1:0:h",
            "text": text, "doc_start": doc_start, "doc_end": doc_end, "dense": dense or [1.0, 0.0, 0.0]}


class _FakeStore:
    def __init__(self, typed=None, all_rows=None):
        self._typed = typed or []
        self._all = all_rows or []
        self.calls = []

    def spans_by_document(self, cid, functions):
        self.calls.append(("typed", cid, tuple(functions)))
        return [r for r in self._typed if r["primary_tag"] in functions]

    def all_spans_by_document(self, cid):
        self.calls.append(("all", cid))
        return self._all


class _FakeEmbedder:
    def __init__(self, qvec):
        self._q = qvec

    def encode_dense(self, _text):
        return self._q


def _extract_factory(value_map):
    class _R:
        def invoke(self, prompt):
            for text, val in value_map.items():
                if text in prompt:
                    return _Extracted(value=val)
            return _Extracted(value=None)

    return lambda _m, _s: _R()


# --- branch 1: in-taxonomy highlight -------------------------------------------------------------

def test_highlight_returns_the_typed_span_set_with_citation():
    store = _FakeStore(typed=[_row("s0", "Governing Law", "governed by Delaware law", 100, 130),
                              _row("s1", "Governing Law", "the courts of Delaware", 200, 225)])
    intent = QueryIntent(clause_types=["Governing Law"], intent="highlight", confidence=0.9)
    res = serve_highlight("what law governs?", "C1", intent, store=store)
    assert res.present is True and res.in_taxonomy is True and res.low_confidence is False
    assert [s.span_id for s in res.spans] == ["s0", "s1"]
    s0 = res.spans[0]
    assert s0.function == "Governing Law" and s0.doc_start == 100 and s0.doc_end == 130
    assert s0.clause_ref == "C1:0:h" and s0.confidence == 0.9 and s0.extracted_value is None


def test_absent_type_is_not_present():
    store = _FakeStore(typed=[_row("s0", "Insurance", "insurance clause", 10, 20)])
    intent = QueryIntent(clause_types=["Non-Compete"], intent="highlight")
    res = serve_highlight("is there a non-compete?", "C1", intent, store=store)
    assert res.present is False and res.spans == []


def test_multi_type_returns_both():
    store = _FakeStore(typed=[_row("s0", "Governing Law", "gov law", 10, 20),
                              _row("s1", "Insurance", "insurance", 30, 40)])
    intent = QueryIntent(clause_types=["Governing Law", "Insurance"], intent="highlight")
    res = serve_highlight("gov law and insurance", "C1", intent, store=store)
    assert {s.span_id for s in res.spans} == {"s0", "s1"}


# --- branch 2: in-taxonomy extract ---------------------------------------------------------------

def test_extract_fills_extracted_value_per_span():
    store = _FakeStore(typed=[_row("s0", "Governing Law", "This Agreement is governed by Delaware law.", 0, 42)])
    intent = QueryIntent(clause_types=["Governing Law"], intent="extract",
                         value_to_extract="the governing law state")
    factory = _extract_factory({"governed by Delaware law": "Delaware"})
    res = serve_highlight("which state's law?", "C1", intent, store=store, structured_factory=factory)
    assert res.intent == "extract"
    assert res.spans[0].extracted_value == "Delaware"


# --- branch 3: discriminate stub (passthrough) ---------------------------------------------------

def test_discriminate_is_passthrough_returns_the_set():
    store = _FakeStore(typed=[_row("s0", "Insurance", "policy A", 10, 20),
                              _row("s1", "Insurance", "policy B", 30, 40)])
    intent = QueryIntent(clause_types=["Insurance"], intent="discriminate",
                         value_condition="the one naming the buyer as additional insured")
    res = serve_highlight("which insurance names the buyer?", "C1", intent, store=store)
    assert res.intent == "discriminate"
    assert [s.span_id for s in res.spans] == ["s0", "s1"]  # stub: whole same-type set returned
    assert res.spans[0].extracted_value is None


# --- branch 4: out-of-taxonomy semantic fallback -------------------------------------------------

def test_out_of_taxonomy_falls_back_to_contract_semantic_ranking_with_low_confidence():
    all_rows = [_row("a", "NONE", "indemnification text", 0, 10, dense=[1.0, 0.0, 0.0]),
                _row("b", "NONE", "force majeure and epidemics", 20, 40, dense=[0.0, 1.0, 0.0]),
                _row("c", "NONE", "notices and addresses", 50, 60, dense=[0.0, 0.0, 1.0])]
    store = _FakeStore(all_rows=all_rows)
    embedder = _FakeEmbedder([0.0, 1.0, 0.0])  # aligned with span "b"
    intent = QueryIntent(clause_types=[], in_taxonomy=False, confidence=0.4)
    res = serve_highlight("what about force majeure?", "C1", intent, store=store,
                          embedder=embedder, fallback_k=2)
    assert res.in_taxonomy is False and res.low_confidence is True and res.present is True
    assert res.spans[0].span_id == "b"  # highest cosine to the query vector
    assert len(res.spans) == 2  # capped at fallback_k
    assert ("all", "C1") in store.calls and not any(c[0] == "typed" for c in store.calls)
