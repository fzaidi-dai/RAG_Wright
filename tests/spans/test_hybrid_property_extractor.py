"""CLS-B (FR-C.6/FR-I.4): HybridPropertyExtractor — classifiers for covered dims, LLM for ONLY the uncovered dims,
skipped entirely when the clause type is fully covered. Hermetic (stub classifiers + stub LLM runnable)."""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import PropertyDimension as D
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.spans.dim_classifier import DimClassifierRegistry
from rag_wright.spans.property_extractor import ExtractedProperty, HybridPropertyExtractor, PropertyExtraction

CK = ChunkId(source_doc_id="C1", chunk_index=0, content_hash="0" * 64)


class _StubDim:
    def __init__(self, dim, ranking):
        self.dim = dim
        self._r = ranking

    def classify(self, text):  # noqa: ARG002
        return self._r


class _StubRunnable:
    """Records each prompt it is asked and returns a fixed PropertyExtraction (the LLM's uncovered answer)."""

    def __init__(self, props):
        self._props = props
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        return PropertyExtraction(properties=self._props)


def _registry():
    return DimClassifierRegistry({
        D.IP_OWNERSHIP: _StubDim(D.IP_OWNERSHIP, [("assigned", 0.9)]),
        D.COVERED_PARTIES: _StubDim(D.COVERED_PARTIES, [("affiliates", 0.8), ("licensee_affiliates", 0.5)]),  # top-2
        D.WARRANTY_SCOPE: _StubDim(D.WARRANTY_SCOPE, [("as_is", 0.9)]),
    })


def test_fully_covered_function_skips_the_llm():
    # "IP Ownership Assignment" -> {ip_ownership, covered_parties}, both covered -> NO LLM call.
    run = _StubRunnable([])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="IP Ownership Assignment", text="… hereby assigns all IP to Company …", span_id="s1")
    assert run.calls == []                                        # the latency win: no LLM for a fully-covered type
    assert {a.dimension for a in rec.assertions} == {D.IP_OWNERSHIP, D.COVERED_PARTIES}
    cp = [a for a in rec.assertions if a.dimension == D.COVERED_PARTIES]
    assert {a.value for a in cp} == {"affiliates", "licensee_affiliates"}   # top-2 soft tags emitted
    assert any(a.confidence == ConfidenceTag.EXTRACTED for a in cp)         # primary EXTRACTED
    assert any(a.confidence == ConfidenceTag.INFERRED for a in cp)          # lower-ranked INFERRED


def test_partial_asks_llm_only_for_uncovered_dims():
    # "Warranty Disclaimer" -> {favorability (uncovered), warranty_scope (covered)}.
    run = _StubRunnable([ExtractedProperty(dimension=D.FAVORABILITY, value="seller_favorable",
                                           confidence=ConfidenceTag.EXTRACTED)])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Warranty Disclaimer", text="… PRODUCT IS SOLD AS IS …", span_id="s2")
    assert len(run.calls) == 1
    prompt = run.calls[0].lower()
    assert "favorability" in prompt and "warranty_scope" not in prompt     # LLM asked ONLY the uncovered dim
    pairs = {(a.dimension, a.value) for a in rec.assertions}
    assert (D.WARRANTY_SCOPE, "as_is") in pairs                            # covered -> classifier
    assert (D.FAVORABILITY, "seller_favorable") in pairs                  # uncovered -> LLM
