"""CLS-C (FR-I.4): ClassifierPropertyExtractor -- the gated, classifier-first Step-3a property extractor.

It runs the function-independent HybridPropertyExtractor (classifiers for the 21 covered dims + ONE residual LLM
call for the 7 numeric/open dims) and then the SAME record-level gates DGClausePropertyExtractor applies:
ADR-0028 `reground` -> ADR-0040 `symbolic_validate` -> optional Layer-3 `semantic_judge` (sync) / `asemantic_judge`
(async). This REPLACES the full-LLM tag-parse extraction for these dims (ADR-0115; no toggle). Hermetic: a stub
hybrid + spied module-level gates, so no model, no checkpoints, no network.
"""

from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import ClausePropertyRecord
from rag_wright.spans import clause_kg_extractor as cke
from rag_wright.spans.clause_kg_extractor import ClassifierPropertyExtractor, classifier_property_extractor

CK = ChunkId(source_doc_id="C1", chunk_index=0, content_hash="0" * 64)


def _rec(fn: str = "Cap On Liability") -> ClausePropertyRecord:
    return ClausePropertyRecord(clause_id=str(CK), function=fn, folio_iri="", assertions=[])


class _StubHybrid:
    """Stands in for HybridPropertyExtractor: returns a fixed raw record, records how it was called."""

    def __init__(self, record: ClausePropertyRecord) -> None:
        self._record = record
        self.calls: list[tuple] = []
        self.acalls: list[tuple] = []

    def __call__(self, *, chunk_id, function, text, span_id="", functions=()):
        self.calls.append((function, text, span_id))
        return self._record

    async def aextract(self, *, chunk_id, function, text, span_id="", functions=()):
        self.acalls.append((function, text, span_id))
        return self._record


def test_call_applies_reground_then_symbolic_gate(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(cke, "reground", lambda r, t: log.append("reground") or r)
    monkeypatch.setattr(cke, "symbolic_validate", lambda r: log.append("symbolic") or r)
    hybrid = _StubHybrid(_rec())
    ex = ClassifierPropertyExtractor(hybrid)  # no semantic judge
    out = ex(chunk_id=CK, function="Governing Law", text="…the clause…", span_id="s1")
    assert log == ["reground", "symbolic"]                 # ADR-0028 then ADR-0040, in order
    assert hybrid.calls == [("Governing Law", "…the clause…", "s1")]  # delegated to the hybrid extractor
    assert isinstance(out, ClausePropertyRecord)


def test_semantic_judge_applied_only_when_provided(monkeypatch):
    log: list[str] = []
    sentinel = _rec()  # sentinel (identity check)
    monkeypatch.setattr(cke, "reground", lambda r, t: r)
    monkeypatch.setattr(cke, "symbolic_validate", lambda r: r)
    monkeypatch.setattr(cke, "semantic_judge", lambda r, t, fn: log.append("semantic") or sentinel)
    hybrid = _StubHybrid(_rec())

    ex = ClassifierPropertyExtractor(hybrid, semantic_judge_fn=lambda *a, **k: None)
    out = ex(chunk_id=CK, function="X", text="…")
    assert log == ["semantic"] and out is sentinel        # Layer-3 runs, its output is returned

    log.clear()
    ClassifierPropertyExtractor(hybrid)(chunk_id=CK, function="X", text="…")
    assert log == []                                       # no judge fn -> semantic gate skipped


async def test_aextract_applies_gates_and_async_judge(monkeypatch):
    log: list[str] = []
    sentinel = _rec()  # sentinel (identity check)
    monkeypatch.setattr(cke, "reground", lambda r, t: log.append("reground") or r)
    monkeypatch.setattr(cke, "symbolic_validate", lambda r: log.append("symbolic") or r)

    async def _asj(r, t, fn):
        log.append("asemantic")
        return sentinel

    monkeypatch.setattr(cke, "asemantic_judge", _asj)
    hybrid = _StubHybrid(_rec())
    ex = ClassifierPropertyExtractor(hybrid, asemantic_judge_fn=lambda *a, **k: None)
    out = await ex.aextract(chunk_id=CK, function="Y", text="…", span_id="s2")
    assert log == ["reground", "symbolic", "asemantic"]    # same cascade on the async path
    assert hybrid.acalls == [("Y", "…", "s2")]             # awaited the hybrid's async twin
    assert out is sentinel


def test_factory_wraps_an_injected_registry_over_the_hybrid(monkeypatch):
    # classifier_property_extractor is what the pipeline calls; injecting a registry avoids live checkpoints
    from rag_wright.spans.dim_classifier import DimClassifierRegistry
    from rag_wright.spans.property_extractor import HybridPropertyExtractor

    ex = classifier_property_extractor(registry=DimClassifierRegistry({}), runnable=object())
    assert isinstance(ex, ClassifierPropertyExtractor)
    assert isinstance(ex._hybrid, HybridPropertyExtractor)
