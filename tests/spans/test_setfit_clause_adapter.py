"""T55/SETFIT-SEG-1: the in-process SetFit ensemble soft-tagger behind the ClauseFunctionClassifier seam.

Live test (skips when the checkpoints aren't present -- they are gitignored/local-only, downloaded from the model
store). Confirms the adapter satisfies the SAME `classify_spans(chunk_text, span_texts) -> list[list[FunctionScore]]`
contract as the LLM/LegalBERT classifiers, emits soft tags for clear clauses, and is robust to empty input.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_wright.contracts.function import FunctionConfidence, FunctionScore, canonical_function

_ROOT = Path(os.getenv("RAG_SETFIT_CLAUSE_DIR", "data/models/setfit_clause"))
_HAVE = (_ROOT / "cap128b_legalbert" / "model_head.pkl").exists()
pytestmark = pytest.mark.skipif(
    not _HAVE, reason="SetFit clause checkpoints not present (gitignored / local-only)")


@pytest.fixture(scope="module")
def clf():
    from rag_wright.spans.clause_function_classifier import production_setfit_clause_classifier

    return production_setfit_clause_classifier()


def test_emits_expected_soft_tags_conforming_to_contract(clf):
    spans = [
        "Either party may terminate this Agreement for convenience upon sixty (60) days prior written notice.",
        "This Agreement shall be governed by and construed in accordance with the laws of the State of New York.",
    ]
    res = clf.classify_spans("shared chunk context", spans)
    assert len(res) == len(spans)
    for tags in res:  # every tag conforms to the FunctionScore contract with a CANONICAL label
        assert all(isinstance(t, FunctionScore) for t in tags)
        assert all(canonical_function(t.function) == t.function for t in tags)
        assert all(isinstance(t.confidence, FunctionConfidence) for t in tags)
    assert "Termination For Convenience" in [t.function for t in res[0]]
    assert "Governing Law" in [t.function for t in res[1]]


def test_empty_and_single_span(clf):
    assert clf.classify_spans("", []) == []
    single = clf.classify("This Agreement shall be governed by the laws of the State of Delaware.")
    assert any(t.function == "Governing Law" for t in single)


def test_span_level_ignores_chunk_context(clf):
    # span-level (like LegalBertClauseAdapter): the chunk_text arg must not change per-span results
    span = ["Licensor hereby grants Licensee a perpetual, irrevocable, worldwide license to use the Software."]
    a = clf.classify_spans("", span)
    b = clf.classify_spans("some unrelated chunk context here", span)
    assert [t.function for t in a[0]] == [t.function for t in b[0]]
