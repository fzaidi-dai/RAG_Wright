"""CLS-B/C rework (FR-C.6/FR-I.4): the FUNCTION-INDEPENDENT hybrid Step-3a property extractor.

The DECIDED design (no toggle, no LLM fallback for classifier dims):
  * classifiers fill EVERY covered dimension, regardless of the clause's function (function is a soft tag, never
    a gate) -- top-k soft tags: rank-0 EXTRACTED, lower ranks INFERRED;
  * ACCEPT-WEAK dims (cap_basis, renewal_mechanism) are emitted AMBIGUOUS (low-confidence, still local);
  * ONE consolidated residual LLM call fills ONLY the 7 numeric/open dims classification cannot produce
    (a number/place/duration); it is NEVER asked for a classifier dim;
  * the 8 corpus-starved closed-vocab dims are NOT extracted here -- they join the CLASSIFIER lane after CLS-F
    data-sourcing, and never the LLM (even a rogue LLM value for one is filtered out).
Hermetic: stub classifiers + stub runnable, no model, no network.
"""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import PropertyDimension as D
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.spans.dim_classifier import DimClassifierRegistry
from rag_wright.spans.property_extractor import (
    ACCEPT_WEAK_DIMS,
    RESIDUAL_LLM_DIMS,
    ExtractedProperty,
    HybridPropertyExtractor,
    PropertyExtraction,
    scoped_dims,
)

CK = ChunkId(source_doc_id="C1", chunk_index=0, content_hash="0" * 64)


class _StubDim:
    def __init__(self, dim, ranking):
        self.dim = dim
        self._r = ranking

    def classify(self, text):  # noqa: ARG002
        return self._r


class _StubRunnable:
    """Records each prompt it is asked and returns a fixed PropertyExtraction (the residual LLM's answer)."""

    def __init__(self, props):
        self._props = props
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt)
        return PropertyExtraction(properties=self._props)


def _registry():
    return DimClassifierRegistry({
        D.IP_OWNERSHIP: _StubDim(D.IP_OWNERSHIP, [("assigned", 0.9)]),
        D.FAVORABILITY: _StubDim(D.FAVORABILITY, [("seller_favorable", 0.8)]),
        D.CARVE_OUT: _StubDim(D.CARVE_OUT, [("fraud", 0.7), ("confidentiality", 0.5)]),  # top-2 soft
        D.CAP_BASIS: _StubDim(D.CAP_BASIS, [("multiple_of_fees", 0.6)]),                  # accept-weak
    })


def test_the_7_residual_dims_are_the_numeric_open_ones():
    # contract guard: the residual LLM set is exactly the 7 extractive dims, none of them classifier-covered
    assert set(RESIDUAL_LLM_DIMS) == {
        D.CAP_QUANTUM, D.JURISDICTION, D.TEMPORAL_BOUND, D.NOTICE_PERIOD,
        D.AUDIT_FREQUENCY, D.COMMITMENT_QUANTUM, D.LD_TRIGGER,
    }
    assert D.CAP_BASIS in ACCEPT_WEAK_DIMS and D.RENEWAL_MECHANISM in ACCEPT_WEAK_DIMS


def test_classifiers_fill_covered_dims_regardless_of_function():
    # a governing-law function must NOT stop ip_ownership/favorability being classified (function never gates)
    run = _StubRunnable([])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Governing Law", text="… assigns all IP; seller-favorable …", span_id="s1")
    dims = {a.dimension for a in rec.assertions}
    assert D.IP_OWNERSHIP in dims and D.FAVORABILITY in dims
    assert rec.function == "Governing Law"  # soft tag carried on the record, not used to scope extraction


def test_residual_llm_asked_only_for_the_7_numeric_dims():
    run = _StubRunnable([ExtractedProperty(dimension=D.JURISDICTION, value="delaware",
                                           confidence=ConfidenceTag.EXTRACTED)])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Cap On Liability", text="… governed by Delaware …", span_id="s2")
    assert len(run.calls) == 1                                   # ONE consolidated residual call
    prompt = run.calls[0].lower()
    for d in RESIDUAL_LLM_DIMS:
        assert d.value in prompt                                 # all 7 numerics asked
    assert "ip_ownership" not in prompt and "favorability" not in prompt  # never a classifier dim
    assert "dispute_method" not in prompt                        # never a starved dim
    assert (D.JURISDICTION, "delaware") in {(a.dimension, a.value) for a in rec.assertions}


def test_the_8_starved_dims_are_never_extracted():
    # even a rogue LLM value for a starved dim is filtered out (only the 7 residual dims are kept)
    run = _StubRunnable([ExtractedProperty(dimension=D.DISPUTE_METHOD, value="arbitration",
                                           confidence=ConfidenceTag.EXTRACTED)])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Governing Law", text="… disputes by arbitration …", span_id="s3")
    assert all(a.dimension != D.DISPUTE_METHOD for a in rec.assertions)


def test_topk_soft_tags_and_confidence():
    run = _StubRunnable([])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Cap On Liability", text="…", span_id="s4")
    co = [a for a in rec.assertions if a.dimension == D.CARVE_OUT]
    assert {a.value for a in co} == {"fraud", "confidentiality"}
    assert any(a.confidence == ConfidenceTag.EXTRACTED for a in co)   # rank-0 primary
    assert any(a.confidence == ConfidenceTag.INFERRED for a in co)    # rank-1 soft tag


def test_accept_weak_dims_downgraded_to_ambiguous():
    run = _StubRunnable([])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    rec = hx(chunk_id=CK, function="Cap On Liability", text="…", span_id="s5")
    cb = [a for a in rec.assertions if a.dimension == D.CAP_BASIS]
    assert cb and all(a.confidence == ConfidenceTag.AMBIGUOUS for a in cb)  # accept-weak -> AMBIGUOUS, not EXTRACTED


def test_soft_function_scoping_limits_the_classifier_lane():
    # CLS-D: a classifier can't abstain, so unscoped every dim fires. Scoping to the function's dims prevents
    # over-emission -- an out-of-scope dim (nonsolicit on a cap clause) is NOT emitted; empty functions -> no scoping.
    reg = DimClassifierRegistry({
        D.CARVE_OUT: _StubDim(D.CARVE_OUT, [("fraud", 0.9)]),                  # applies to Cap On Liability
        D.NONSOLICIT_TARGET: _StubDim(D.NONSOLICIT_TARGET, [("employees", 0.99)]),  # does NOT
        D.IP_OWNERSHIP: _StubDim(D.IP_OWNERSHIP, [("retained", 0.9)]),          # does NOT
    })
    hx = HybridPropertyExtractor(reg, runnable=_StubRunnable([]))
    scoped = hx(chunk_id=CK, function="Cap On Liability", text="…", span_id="s", functions=("Cap On Liability",))
    dims = {a.dimension for a in scoped.assertions}
    assert D.CARVE_OUT in dims                              # in-scope -> classified
    assert D.NONSOLICIT_TARGET not in dims and D.IP_OWNERSHIP not in dims  # out-of-scope -> NOT emitted
    # union over top-k functions: adding an IP function brings ip_ownership back into scope
    scoped2 = hx(chunk_id=CK, function="Cap On Liability", text="…",
                 functions=("Cap On Liability", "IP Ownership Assignment"))
    assert D.IP_OWNERSHIP in {a.dimension for a in scoped2.assertions}
    # no functions -> no scoping (function-independent fallback: every covered dim runs)
    unscoped = hx(chunk_id=CK, function="Cap On Liability", text="…")
    assert {D.CARVE_OUT, D.NONSOLICIT_TARGET, D.IP_OWNERSHIP} <= {a.dimension for a in unscoped.assertions}
    assert scoped_dims(()) is None                          # empty -> no scoping sentinel


def test_single_residual_llm_call_always_fires_exactly_once():
    # the 7 residual dims are NEVER classifier-covered, so the one residual call always fires (never skipped)
    run = _StubRunnable([])
    hx = HybridPropertyExtractor(_registry(), runnable=run)
    hx(chunk_id=CK, function="IP Ownership Assignment", text="…", span_id="s6")
    assert len(run.calls) == 1
