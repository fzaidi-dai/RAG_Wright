"""Graph extraction (FR-C.6/FR-I.4): the GP-1B docling-graph party/relational extractor over chunks (ADR-0035).

Hermetic tests inject a stub `extract_fn` (no docling-graph, no network) to prove the extractor produces
ontology-conforming facts anchored to chunk_id with a confidence tag, and that the capability runs extraction
concurrently (FR-I.6). The live docling-graph path is exercised by the GP-1B / ingest scripts, not here.
"""

from __future__ import annotations

import threading
import time

from rag_wright.capabilities.graph_extraction import (
    DoclingGraphExtractor,
    extract_chunks_sync,
    parties_to_extraction,
    register_graph_extraction,
)
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.contracts.extraction import ExtractionResult, run_extractors
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.contracts.provenance import ConfidenceTag

_CID = ChunkId.of("docA", 0, "chunk text")


class _Party:
    def __init__(self, name: str) -> None:
        self.name = name


class _ContractParties:
    """A stub ContractParties-shaped result (has `.parties[].name`)."""

    def __init__(self, names: list[str]) -> None:
        self.parties = [_Party(n) for n in names]


def _extract_fn(names, *, none: bool = False):
    return lambda text: (None if none else _ContractParties(names))


# --- DoclingGraphExtractor: parties -> ORGANIZATION mentions + structural CONTRACTS_WITH (GP-1B) ---


def test_extractor_emits_party_mentions_and_a_contracts_with_edge():
    result = DoclingGraphExtractor(_extract_fn(["Acme Corp", "Beta LLC", "Acme Corp"])).extract(_CID, "text")

    assert result.chunk_id == _CID
    assert {m.text for m in result.entity_mentions} == {"Acme Corp", "Beta LLC"}  # dedup, order-stable
    assert all(m.entity_type == "Organization" for m in result.entity_mentions)
    assert all(m.confidence is ConfidenceTag.EXTRACTED for m in result.entity_mentions)
    assert len(result.relationship_facts) == 1  # one CONTRACTS_WITH between the two distinct parties
    edge = result.relationship_facts[0]
    assert edge.relationship_type == "Contracts With"
    assert {edge.source_ref, edge.target_ref} == {"Acme Corp", "Beta LLC"}
    assert edge.confidence is ConfidenceTag.EXTRACTED
    assert edge.provenance.chunk_id == _CID  # anchored to the chunk


def test_extractor_single_party_emits_no_edge():
    result = DoclingGraphExtractor(_extract_fn(["Acme Corp"])).extract(_CID, "text")
    assert result.relationship_facts == []  # need >= 2 parties for a CONTRACTS_WITH edge


def test_extractor_no_parties_returns_empty_result_not_error():
    assert DoclingGraphExtractor(_extract_fn([], none=True)).extract(_CID, "text").entity_mentions == []
    assert DoclingGraphExtractor(_extract_fn([])).extract(_CID, "text").entity_mentions == []


# --- the seam merges results; the capability runs concurrently (FR-I.6) ---------------------------


def test_run_extractors_merges_multiple_extractors():
    a = DoclingGraphExtractor(_extract_fn(["Acme Corp", "Beta LLC"]))
    b = DoclingGraphExtractor(_extract_fn(["Gamma Inc"]))
    merged = run_extractors([a, b], _CID, "text")
    assert merged.chunk_id == _CID
    assert {m.text for m in merged.entity_mentions} == {"Acme Corp", "Beta LLC", "Gamma Inc"}
    assert len(merged.relationship_facts) == 1  # the CONTRACTS_WITH from extractor a's two parties


class _ProbeExtractor:
    name = "probe"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def extract(self, chunk_id: ChunkId, text: str) -> ExtractionResult:
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        time.sleep(0.03)  # stand in for a GPU/network call so extractions overlap
        with self._lock:
            self.inflight -= 1
        return ExtractionResult(chunk_id=chunk_id)


def test_extract_chunks_runs_concurrently_bounded_by_the_semaphore():
    probe = _ProbeExtractor()
    items = [(ChunkId.of("docA", i, f"t{i}"), f"t{i}") for i in range(12)]

    results = extract_chunks_sync(items, extractors=[probe], max_concurrency=4)

    assert len(results) == 12
    assert probe.max_inflight == 4  # concurrent AND bounded exactly by the semaphore (FR-I.6)


def test_extract_chunks_serial_reaches_only_one_in_flight():
    probe = _ProbeExtractor()
    items = [(ChunkId.of("docA", i, f"t{i}"), f"t{i}") for i in range(4)]
    extract_chunks_sync(items, extractors=[probe], max_concurrency=1)
    assert probe.max_inflight == 1


def test_parties_to_extraction_shared_fact_shape():
    er = parties_to_extraction(_CID, [" Acme Corp ", "Acme Corp", "", "Beta Inc"])  # dup + whitespace + empty
    assert {m.text for m in er.entity_mentions} == {"Acme Corp", "Beta Inc"}
    assert len(er.relationship_facts) == 1


# --- issue 0027: corporate AFFILIATION extraction (AFFILIATE_OF) ---------------------------------


def test_affiliations_to_extraction_emits_both_org_mentions_and_an_affiliate_of_edge():
    from rag_wright.capabilities.graph_extraction import affiliations_to_extraction

    er = affiliations_to_extraction(_CID, [("Acme Holdings Ltd", "Acme Corp"), ("", "X"), ("Y", "Y")])
    # both orgs become ORGANIZATION mentions (endpoints must resolve to nodes); empty + self pairs dropped
    assert {m.text for m in er.entity_mentions} == {"Acme Holdings Ltd", "Acme Corp"}
    assert len(er.relationship_facts) == 1
    fact = er.relationship_facts[0]
    assert fact.relationship_type == "Affiliate Of"
    assert fact.source_ref == "Acme Holdings Ltd" and fact.target_ref == "Acme Corp"  # NOT merged; edge added


async def test_aextract_affiliations_pre_filter_skips_the_llm_when_no_cue():
    from rag_wright.capabilities import graph_extraction as gx

    called = {"n": 0}

    def _boom(*a, **k):  # the LLM path must not be reached when there is no affiliation cue word
        called["n"] += 1
        raise AssertionError("build_tag_structured called despite no affiliation cue")

    # no cue in the text -> returns [] without importing/calling the model path
    out = await gx.aextract_affiliations("Northwind Trading Ltd and Beta Inc enter this agreement.")
    assert out == [] and called["n"] == 0


async def test_aextract_affiliations_parses_pairs_on_a_cue_hit(monkeypatch):
    from rag_wright.capabilities import graph_extraction as gx
    from rag_wright.models import tag_structured

    class _R:
        async def ainvoke(self, _prompt):
            return gx.Affiliations(affiliations=[
                gx.Affiliation(organization="Acme Holdings Ltd", affiliate_of="Acme Corp"),
                gx.Affiliation(organization="", affiliate_of="skip")])  # empty side dropped

    monkeypatch.setattr(tag_structured, "build_tag_structured", lambda *a, **k: _R())
    out = await gx.aextract_affiliations("Acme Holdings Ltd, an affiliate of Acme Corp, agrees ...")
    assert out == [("Acme Holdings Ltd", "Acme Corp")]


async def test_aextract_affiliations_degrades_to_empty_on_parse_failure(monkeypatch):
    from rag_wright.capabilities import graph_extraction as gx
    from rag_wright.models import tag_structured

    class _R:
        async def ainvoke(self, _prompt):
            raise RuntimeError("parse blip")

    monkeypatch.setattr(tag_structured, "build_tag_structured", lambda *a, **k: _R())
    assert await gx.aextract_affiliations("wholly-owned subsidiary of Foo") == []  # cue present, LLM fails -> []


def test_registers_under_fr_c_6():
    registry = CapabilityRegistry()
    register_graph_extraction(registry)
    reg = registry.get("graph_extraction")
    assert reg.name == "graph_extraction"
    assert reg.contract is ExtractionResult
    assert reg.kind == "subgraph"
