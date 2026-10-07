"""T60 (FR-C.3, ADR-0026): the keyword pre-filter + LLM-confirm plumbing for the 3 new classes.

Hermetic: the keyword filter is pure, and the confirmer is exercised with a stub (no model download,
no network).
"""

from __future__ import annotations

from rag_wright.packs.contracts.spans.new_function_labels import (
    NewFunctionConfirmer,
    NewFunctionTag,
    keyword_candidates,
    label_prompt,
)


def test_keyword_prefilter_flags_each_new_class():
    assert "Indemnification" in keyword_candidates("Supplier shall indemnify and hold harmless Buyer")
    assert "Indirect/Consequential Damages Waiver" in keyword_candidates(
        "In no event shall either party be liable for consequential or lost profits"
    )
    assert "Warranty Disclaimer" in keyword_candidates(
        'The goods are provided "AS IS" and Seller disclaims the warranty of merchantability'
    )


def test_keyword_prefilter_empty_on_unrelated_text():
    assert keyword_candidates("This Agreement is governed by the laws of the State of New York") == frozenset()


def test_prefilter_can_flag_multiple_candidates():
    # a span mentioning both an indemnity and a damages cap is a legitimate multi-candidate -> LLM decides
    cands = keyword_candidates("Indemnification obligations survive; in no event are consequential damages owed")
    assert {"Indemnification", "Indirect/Consequential Damages Waiver"} <= cands


class _StubConfirmer:
    """A confirmer that returns whatever single candidate was passed (or NONE when ambiguous)."""

    def __call__(self, text: str, candidates: frozenset[str]) -> NewFunctionTag:
        if candidates == {"Indemnification"}:
            return NewFunctionTag.INDEMNIFICATION
        return NewFunctionTag.NONE


def test_confirmer_protocol_and_prompt_lists_candidates():
    confirmer: NewFunctionConfirmer = _StubConfirmer()  # structural typing: the stub satisfies the seam
    assert confirmer("Supplier shall indemnify Buyer", frozenset({"Indemnification"})) is NewFunctionTag.INDEMNIFICATION
    assert confirmer("ambiguous", frozenset({"Warranty Disclaimer", "Indemnification"})) is NewFunctionTag.NONE
    prompt = label_prompt("some span text", frozenset({"Indemnification", "Warranty Disclaimer"}))
    assert "Indemnification" in prompt and "Warranty Disclaimer" in prompt and "some span text" in prompt
