"""LG-2: the graph_extraction PARALLEL subgraph -- hermetic (stub extractors, no LLM/spaCy)."""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.contracts.extraction import EntityMention, ExtractionResult
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.subgraphs.graph_extraction import build_graph_extraction

_FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.0)
_CID = ChunkId.of("doc", 0, "Acme and Beta contract")


def _mention(text: str) -> EntityMention:
    return EntityMention(text=text, entity_type="Organization", confidence=ConfidenceTag.EXTRACTED)


class _StubExtractor:
    def __init__(self, name: str, mentions=(), fail: bool = False) -> None:
        self.name = name
        self._mentions = list(mentions)
        self._fail = fail
        self.calls = 0

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        self.calls += 1
        if self._fail:
            raise RuntimeError("extractor blip")
        return ExtractionResult(chunk_id=chunk_id, entity_mentions=self._mentions)


def test_parallel_extractors_merge_into_one_result():
    a = _StubExtractor("ner", [_mention("Acme")])
    b = _StubExtractor("contract", [_mention("Beta")])
    graph = build_graph_extraction([a, b], retry_policy=_FAST_RETRY)
    out = graph.invoke({"chunk_id": _CID, "text": "Acme and Beta contract"})
    texts = {m.text for m in out["result"].entity_mentions}
    assert texts == {"Acme", "Beta"}  # both extractors ran (parallel) and merged
    assert a.calls == 1 and b.calls == 1


def test_failing_extractor_degrades_to_empty_without_dropping_the_chunk():
    good = _StubExtractor("ner", [_mention("Acme")])
    bad = _StubExtractor("contract", fail=True)  # always fails
    graph = build_graph_extraction([good, bad], retry_policy=_FAST_RETRY)
    out = graph.invoke({"chunk_id": _CID, "text": "x"})
    texts = {m.text for m in out["result"].entity_mentions}
    assert texts == {"Acme"}  # the good extractor's facts survive; the bad one degraded to empty
    assert bad.calls == 3  # retried up to max_attempts, then contributed an empty result


def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.graph_extraction import register_graph_extraction_subgraph

    reg = CapabilityRegistry()
    register_graph_extraction_subgraph(reg)
    assert reg.get("graph_extraction").kind == "subgraph"
