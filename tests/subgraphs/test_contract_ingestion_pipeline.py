"""LG-3d: the `contract_ingestion_pipeline` composite -- hermetic (stub stages + fake adapter, no LLM/DB).

The GENERIC ingestion pipeline (corpus-agnostic): chunk -> [extract_clauses || extract_graph] -> resolve ->
write, per document, with a per-document dead-letter so one bad document never kills the corpus ingest. The
corpus driver maps a `CorpusAdapter`'s documents through the pipeline then runs party_clause_linking once.
Adding a corpus = writing one adapter, never re-implementing the flow.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

from rag_wright.subgraphs.contract_ingestion_pipeline import (
    IngestionReport,
    SourceDocument,
    per_contract_graph_extraction,
    seed_chunk_cache,
    seed_party_cache,
)

_FAST_RETRY = RetryPolicy(max_attempts=2, initial_interval=0.0)




















def test_registers_as_a_subgraph():
    from rag_wright.capabilities.registry import CapabilityRegistry
    from rag_wright.subgraphs.contract_ingestion_pipeline import register_contract_ingestion_pipeline

    reg = CapabilityRegistry()
    register_contract_ingestion_pipeline(reg)
    assert reg.get("contract_ingestion_pipeline").kind == "subgraph"
    assert reg.get("contract_ingestion_pipeline").contract is IngestionReport


# --- INGEST-REFACTOR (a): per-contract GP-1B + cache reuse ---------------------------------------------------

def test_seed_party_cache_canonicalizes_keys_and_is_idempotent(tmp_path):
    import json

    legacy = tmp_path / "dg_extracted_parties.json"
    # the legacy key is the RAW title (spaces/commas); the pipeline addresses by canonical source_doc_id (HYG-1)
    legacy.write_text(json.dumps({"ACME CO_01_2020-EX-10-SUPPLY AGREEMENT": ["Acme Co.", "Beta LLC"]}))
    party_dir = tmp_path / "graph_parties"

    assert seed_party_cache(party_dir, legacy) == 1
    seeded = party_dir / "ACME_CO_01_2020-EX-10-SUPPLY_AGREEMENT.json"  # canonicalized: spaces -> underscores
    assert json.loads(seeded.read_text()) == ["Acme Co.", "Beta LLC"]
    assert seed_party_cache(party_dir, legacy) == 0  # idempotent: never re-writes an existing entry


def test_seed_party_cache_missing_legacy_is_noop(tmp_path):
    assert seed_party_cache(tmp_path / "graph_parties", tmp_path / "does_not_exist.json") == 0


def test_seed_chunk_cache_copies_manifests_only_once(tmp_path):
    legacy = tmp_path / "legacy_chunks"
    legacy.mkdir()
    (legacy / "DocA.deadbeefcafe0001.chunks.json").write_text("{}")
    (legacy / "skip.txt").write_text("not a manifest")
    chunk_dir = tmp_path / "chunks"

    assert seed_chunk_cache(chunk_dir, legacy) == 1  # only the .chunks.json manifest
    assert (chunk_dir / "DocA.deadbeefcafe0001.chunks.json").exists()
    assert seed_chunk_cache(chunk_dir, legacy) == 0  # idempotent


def test_per_contract_graph_reuses_seeded_names_without_extracting(tmp_path):
    import json

    party_dir = tmp_path / "graph_parties"
    party_dir.mkdir()
    (party_dir / "C1.json").write_text(json.dumps(["Acme Co.", "Beta LLC"]))

    def _must_not_extract(_text):
        raise AssertionError("names_fn called despite a seeded cache hit")

    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C1", text="t"), party_dir=party_dir, names_fn=_must_not_extract)

    assert len(out) == 1  # ONE ExtractionResult per contract (not per chunk)
    result = out[0]
    assert [m.text for m in result.entity_mentions] == ["Acme Co.", "Beta LLC"]
    assert len(result.relationship_facts) == 1  # a CONTRACTS_WITH edge between the two parties
    assert result.chunk_id.source_doc_id == "C1"  # provenance stays on the contract (KG-7 join)


def test_per_contract_graph_extracts_once_then_caches(tmp_path):
    party_dir = tmp_path / "graph_parties"
    calls = {"n": 0}

    def _extract(_text):
        calls["n"] += 1
        return ["Acme Co."]

    doc = SourceDocument(source_doc_id="C2", text="body")
    first = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)
    second = per_contract_graph_extraction(doc, party_dir=party_dir, names_fn=_extract)

    assert calls["n"] == 1  # extracted ONCE; the second call served the freshly-written cache
    assert len(first) == len(second) == 1
    assert [m.text for m in first[0].entity_mentions] == ["Acme Co."]
    assert first[0].relationship_facts == []  # a lone party yields a mention but no edge


def test_per_contract_graph_no_parties_yields_no_extraction(tmp_path):
    out = per_contract_graph_extraction(
        SourceDocument(source_doc_id="C3", text="t"), party_dir=tmp_path, names_fn=lambda _t: [])
    assert out == []


# --- PARTY-TO-MANY-TO-MANY durability: the corpus link step must NOT revert PARTY_TO to 1-to-1 on re-ingest ---


class _LinkStore:
    def __init__(self, contracts, entities):
        self._contracts, self._entities, self.written = contracts, entities, None

    def all_contracts(self):
        return self._contracts

    def all_entities(self):
        return self._entities

    def write_party_contract_links(self, links):
        self.written = list(links)


def test_corpus_party_link_fn_defaults_to_many_to_many(tmp_path):
    # the fix: the corpus link step reads the GP-1B mention cache so a party links to EVERY contract it signed
    # (a re-ingest must not silently revert 1278 many-to-many edges to the 1-to-1 join). CORPUS-GENERIC helper.
    import json

    from rag_wright.subgraphs.contract_ingestion_pipeline import corpus_party_link_fn

    path = tmp_path / "dg_extracted_parties.json"
    path.write_text(json.dumps({"C1": ["Acme Corporation"], "C2": ["Acme Corporation"]}), encoding="utf-8")
    store = _LinkStore([{"contract_id": "C1"}, {"contract_id": "C2"}],
                       [{"entity_id": "CIK1", "name": "Acme Corporation", "chunk_id": ""}])

    n = corpus_party_link_fn(store, path)()

    assert n == 2  # Acme -> BOTH contracts (many-to-many), not one last-write-wins edge
    assert {(link.entity_id, link.contract_id) for link in store.written} == {("CIK1", "C1"), ("CIK1", "C2")}


def test_corpus_party_link_fn_falls_back_to_provenance_join_without_cache(tmp_path):
    # graceful: no mention cache -> the single-provenance KG-7 join (still links via chunk_id provenance)
    from rag_wright.subgraphs.contract_ingestion_pipeline import corpus_party_link_fn

    store = _LinkStore([{"contract_id": "C1"}],
                       [{"entity_id": "CIK1", "name": "Acme", "chunk_id": "C1:0:" + "a" * 16}])

    n = corpus_party_link_fn(store, tmp_path / "absent.json")()

    assert n == 1
    assert {(link.entity_id, link.contract_id) for link in store.written} == {("CIK1", "C1")}


# --- PROD-3 lossless invariant (ADR-0050): no silent partial success -------------------------------------------






