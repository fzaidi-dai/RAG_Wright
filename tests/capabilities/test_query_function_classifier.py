"""KG-5e lever (b): the query->function classifier normalizes at the boundary (hermetic, no LLM)."""

from __future__ import annotations

from rag_wright.packs.contracts.capabilities.query_function_classifier import (
    _FunctionChoice,
    classify_query_functions,
)


def _factory(labels):
    """A structured_factory stub whose runnable returns a fixed _FunctionChoice."""

    class _Runnable:
        def invoke(self, _prompt):
            return _FunctionChoice(clause_types=labels)

    def _make(_model_id, _schema):
        return _Runnable()

    return _make


def test_canonicalizes_dedupes_and_truncates_to_k():
    # mixed casing + a duplicate + an off-taxonomy label; k=2
    got = classify_query_functions(
        "q", "m", k=2, structured_factory=_factory(["cap on liability", "Governing Law", "Cap On Liability", "Nonsense"])
    )
    assert got == ["Cap On Liability", "Governing Law"]


def test_drops_off_taxonomy_labels():
    got = classify_query_functions("q", "m", k=3, structured_factory=_factory(["Totally Made Up", "xyz"]))
    assert got == []


def test_none_emit_degrades_to_empty():
    class _NoneRunnable:
        def invoke(self, _prompt):
            return None

    got = classify_query_functions("q", "m", structured_factory=lambda _m, _s: _NoneRunnable())
    assert got == []
