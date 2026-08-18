"""ASYNC-B2b (ADR-0057): the async clause extractor (`DGClausePropertyExtractor.aextract`) and the async
semantic judge (`asemantic_judge` / `build_asemantic_judge_fn`). Hermetic -- injected fns, no model.
"""
from __future__ import annotations

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.property import PropertyDimension
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.ontology import clause_template as ct
from rag_wright.spans.clause_kg_extractor import DGClausePropertyExtractor
from rag_wright.spans.semantic_judge import SemanticVerdict, asemantic_judge, build_asemantic_judge_fn

_CID = ChunkId.of("contract-x", 0, "cap clause text")


async def test_aextract_grounds_exactly_like_call():
    async def _aextract(_text):
        return ct.Clause(document_reference="8", excepts=[ct.ExceptionModel.FRAUD])

    ext = DGClausePropertyExtractor(lambda _t: None, aextract_fn=_aextract)
    rec = await ext.aextract(chunk_id=_CID, function="Cap On Liability",
                             text="except in the case of fraud", span_id="s")
    fraud = [a for a in rec.assertions if a.value == "fraud"]
    assert fraud and fraud[0].confidence == ConfidenceTag.EXTRACTED  # cue present -> stays EXTRACTED (as __call__)


async def test_aextract_empty_record_on_none():
    async def _aextract(_text):
        return None

    ext = DGClausePropertyExtractor(lambda _t: None, aextract_fn=_aextract)
    rec = await ext.aextract(chunk_id=_CID, function="Cap On Liability", text="x", span_id="s")
    assert rec.assertions == [] and rec.clause_id == str(_CID)


def _mutual_record():
    return DGClausePropertyExtractor(
        lambda _t: ct.Clause(document_reference="1", has_mutuality=ct.Mutuality.MUTUAL)
    )(chunk_id=_CID, function="Non-Disparagement", text="mutual", span_id="s")


async def test_asemantic_judge_downgrades_a_refuted_semantic_assertion():
    rec = _mutual_record()
    assert [a.confidence for a in rec.assertions if a.dimension == PropertyDimension.MUTUALITY] == \
        [ConfidenceTag.EXTRACTED]

    async def _refute(_dim, _val, _text):
        return SemanticVerdict(supported=False, reason="no")

    out = await asemantic_judge(rec, "mutual", _refute)
    assert [a.confidence for a in out.assertions if a.dimension == PropertyDimension.MUTUALITY] == \
        [ConfidenceTag.AMBIGUOUS]


async def test_asemantic_judge_noop_when_no_semantic_targets():
    async def _judge(*_a):
        raise AssertionError("the judge must not be called when there is nothing semantic to judge")

    empty = DGClausePropertyExtractor(lambda _t: None)(chunk_id=_CID, function="Cap On Liability", text="x")
    assert await asemantic_judge(empty, "x", _judge) is empty


async def test_build_asemantic_judge_fn_uses_the_async_seam():
    class _FakeAsync:
        async def ainvoke(self, _prompt):
            return SemanticVerdict(supported=True, reason="ok")

    ajudge = build_asemantic_judge_fn("m", structured_factory=lambda *_a: _FakeAsync())
    verdict = await ajudge(PropertyDimension.MUTUALITY, "mutual", "text")
    assert verdict.supported
