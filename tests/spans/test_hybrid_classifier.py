"""Step-5b: the LLM-hybrid classifier routing + the route-family map. Hermetic (fake BERT + fake confirmer)."""

from __future__ import annotations

from rag_wright.spans.function_families import route_family
from rag_wright.spans.hybrid_classifier import HybridFunctionClassifier


# --- route-family map -----------------------------------------------------------------------------

def test_route_family_membership():
    fam = route_family("Notice Period To Terminate Renewal")
    assert fam is not None and "Renewal Term" in fam
    assert route_family("Cap On Liability") is None  # liability = KEEP LegalBERT (not routed)
    assert route_family("Change Of Control") is None  # control = KEEP


def test_route_family_case_insensitive():
    assert route_family("Ip Ownership Assignment") == route_family("IP Ownership Assignment")  # CUAD vs canonical
    assert route_family("Ip Ownership Assignment") is not None


# --- hybrid routing -------------------------------------------------------------------------------

class _FakeBert:
    def __init__(self, topk):
        self._topk = topk

    def classify_topk(self, texts, *, k=2, batch_size=32):
        return self._topk[: len(texts)]


def _confirmer(mapping):
    def _c(text, candidates):
        return mapping.get(text, "NONE")
    return _c


def test_routes_when_top2_are_route_siblings_and_uses_confirmer():
    bert = _FakeBert([["License Grant", "Irrevocable Or Perpetual License"]])  # siblings in a ROUTE family
    hy = HybridFunctionClassifier(bert, confirmer=_confirmer({"s": "Irrevocable Or Perpetual License"}))
    assert hy.classify(["s"]) == ["Irrevocable Or Perpetual License"]  # LLM overrode LegalBERT's top-1


def test_keeps_legalbert_when_top2_not_siblings():
    bert = _FakeBert([["License Grant", "Governing Law"]])  # top-2 not a sibling
    hy = HybridFunctionClassifier(bert, confirmer=_confirmer({"s": "SHOULD_NOT_BE_USED"}))
    assert hy.classify(["s"]) == ["License Grant"]  # kept LegalBERT, no LLM call


def test_keeps_legalbert_for_non_route_family_even_if_siblings():
    bert = _FakeBert([["Uncapped Liability", "Cap On Liability"]])  # siblings but liability = KEEP family
    hy = HybridFunctionClassifier(bert, confirmer=_confirmer({"s": "SHOULD_NOT_BE_USED"}))
    assert hy.classify(["s"]) == ["Uncapped Liability"]  # not routed


def test_confirmer_none_yields_none():
    bert = _FakeBert([["Non-Compete", "No-Solicit Of Customers"]])  # route family (restriction)
    hy = HybridFunctionClassifier(bert, confirmer=_confirmer({}))  # returns NONE
    assert hy.classify(["s"]) == ["NONE"]


def test_mixed_batch_and_canonicalization():
    bert = _FakeBert([
        ["Ip Ownership Assignment", "Joint Ip Ownership"],   # route (IP family), CUAD-cased -> canonicalize
        ["Governing Law", "Insurance"],                       # keep, distinct
    ])
    hy = HybridFunctionClassifier(bert, confirmer=_confirmer({"a": "IP Ownership Assignment"}))
    assert hy.classify(["a", "b"]) == ["IP Ownership Assignment", "Governing Law"]


def test_targeted_routes_only_when_a_target_is_in_top2():
    targets = frozenset({"Irrevocable Or Perpetual License"})
    # top-2 siblings in a route family, and a target IS in the top-2 -> routed
    b1 = _FakeBert([["License Grant", "Irrevocable Or Perpetual License"]])
    hy1 = HybridFunctionClassifier(b1, confirmer=_confirmer({"s": "Irrevocable Or Perpetual License"}),
                                   targets=targets)
    assert hy1.classify(["s"]) == ["Irrevocable Or Perpetual License"]
    # top-2 siblings in the SAME route family but NO target present -> NOT routed (keeps LegalBERT)
    b2 = _FakeBert([["License Grant", "Exclusivity"]])
    hy2 = HybridFunctionClassifier(b2, confirmer=_confirmer({"s": "SHOULD_NOT_BE_USED"}), targets=targets)
    assert hy2.classify(["s"]) == ["License Grant"]


def test_empty():
    assert HybridFunctionClassifier(_FakeBert([]), confirmer=_confirmer({})).classify([]) == []
