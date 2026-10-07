"""Step-4: the scarce-class keyword pre-filter + confirmer validation. Hermetic (no LLM)."""

from __future__ import annotations

from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS
from rag_wright.packs.contracts.spans.scarce_function_labels import (
    NONE_LABEL,
    SCARCE_KEYWORDS,
    SeamScarceConfirmer,
    ScarceLabel,
    scarce_candidates,
)


def test_all_keyword_keys_are_canonical_function_labels():
    assert set(SCARCE_KEYWORDS) <= set(FUNCTION_LABELS)


def test_scarce_candidates_hit():
    assert "Irrevocable Or Perpetual License" in scarce_candidates("a perpetual, irrevocable license to use")
    assert "Non-Disparagement" in scarce_candidates("shall not disparage the other party")
    assert "Third Party Beneficiary" in scarce_candidates("there is no third party beneficiary to this Agreement")


def test_scarce_candidates_case_insensitive_and_empty():
    assert "Most Favored Nation" in scarce_candidates("MOST FAVORED nation pricing applies")
    assert scarce_candidates("this clause is about payment of invoices") == frozenset()


def test_confirmer_maps_offlist_or_noncanonical_label_to_none():
    # inject a fake runnable so no model is built/called
    conf = SeamScarceConfirmer.__new__(SeamScarceConfirmer)
    conf._retries = 3

    class _R:
        def __init__(self, label):
            self._label = label

        def invoke(self, _prompt):
            return ScarceLabel(label=self._label)

    cands = frozenset({"Non-Disparagement"})
    conf._runnable = _R("Non-Disparagement")
    assert conf("x", cands) == "Non-Disparagement"  # in-candidate -> kept
    conf._runnable = _R("Governing Law")
    assert conf("x", cands) == NONE_LABEL  # canonical but NOT a candidate -> NONE
    conf._runnable = _R("Totally Made Up")
    assert conf("x", cands) == NONE_LABEL  # non-canonical -> NONE


def test_confirmer_retries_on_none_then_raises():
    conf = SeamScarceConfirmer.__new__(SeamScarceConfirmer)
    conf._retries = 2

    class _NoneR:
        def invoke(self, _prompt):
            return None

    conf._runnable = _NoneR()
    try:
        conf("x", frozenset({"Non-Disparagement"}))
        assert False, "expected an error after retries"
    except Exception:
        pass
