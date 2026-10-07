"""INGEST-LLM-CLASSIFIER (ADR-0048) Phase A: the pure reclassify logic. Hermetic -- injected classifier."""

from __future__ import annotations

from rag_wright.packs.contracts.schemas.function import NO_FUNCTION, FunctionConfidence, FunctionScore
from rag_wright.packs.contracts.spans.reclassify import ReclassDelta, reclassify_chunk


def _fs(f, c=FunctionConfidence.HIGH):
    return FunctionScore(function=f, confidence=c)


class _Clf:
    def __init__(self, per_span):
        self._per_span = per_span

    def classify_spans(self, chunk_text, span_texts):  # noqa: ARG002 - chunk_text is the shared context
        return self._per_span


def test_reclassify_maps_only_existing_clauses_and_flags_flips():
    spans = [("c:0#0", "force-majeure text"), ("c:0#1", "cap text"), ("c:0#2", "not a clause")]
    existing = {"c:0#0": ("cid0", "Cap On Liability"), "c:0#1": ("cid1", "Cap On Liability")}  # #2 has no Clause
    clf = _Clf([
        [],                              # span 0 -> now NO function (was mislabeled Cap)
        [_fs("Cap On Liability")],       # span 1 -> stays Cap
        [_fs("Governing Law")],          # span 2 -> ignored (no existing Clause)
    ])
    out = reclassify_chunk("chunk ctx", spans, existing, clf)
    assert [(r.clause_id, r.old_function, r.new_primary, r.primary_flipped) for r in out] == [
        ("cid0", "Cap On Liability", NO_FUNCTION, True),   # flipped Cap -> NONE
        ("cid1", "Cap On Liability", "Cap On Liability", False),  # unchanged
    ]


def test_reclassify_multi_label_scores_preserved():
    spans = [("c:0#0", "cap and indemnity")]
    existing = {"c:0#0": ("cid0", "Indemnification")}
    clf = _Clf([[_fs("Cap On Liability"), _fs("Indemnification", FunctionConfidence.MEDIUM)]])
    out = reclassify_chunk("ctx", spans, existing, clf)
    assert out[0].new_primary == "Cap On Liability"          # primary = first
    assert [f.function for f in out[0].new_scores] == ["Cap On Liability", "Indemnification"]
    assert out[0].primary_flipped is True                    # Indemnification -> Cap On Liability


def test_delta_aggregates_flips_and_transitions():
    spans = [("c:0#0", "a"), ("c:0#1", "b"), ("c:0#2", "c")]
    existing = {"c:0#0": ("i0", "Cap On Liability"), "c:0#1": ("i1", "Insurance"), "c:0#2": ("i2", "Audit Rights")}
    clf = _Clf([[], [_fs("Insurance")], [_fs("Governing Law")]])  # flip->NONE, unchanged, flip Audit->Governing
    delta = ReclassDelta()
    for rc in reclassify_chunk("ctx", spans, existing, clf):
        delta.add(rc)
    assert (delta.total, delta.unchanged, delta.flipped, delta.to_none) == (3, 1, 2, 1)
    assert delta.transitions[("Cap On Liability", NO_FUNCTION)] == 1
    assert delta.transitions[("Audit Rights", "Governing Law")] == 1


def test_empty_spans_no_crash():
    assert reclassify_chunk("ctx", [], {}, _Clf([])) == []
