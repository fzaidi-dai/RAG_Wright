"""ADR-0117 DD-1b: the contract domain's store extension.

Composes a generic `Store` (`kg_write`/`kg_read`) to write the typed clause KG (KG-3), the legacy flat property
graph, and contract metadata -- so the engine store imports no contract contract. The clause/value/contract vertex
types + the dimension->edge map + predicate IRIs are pack-authoritative (`contract_bridge.ttl`, ADR-0067 P5a/P5b);
`kg_write` encodes each field per its declared storage type (e.g. `functions` as a JSON string). Domain-native
values in; the store owns all wire encoding."""
from __future__ import annotations

from rag_wright.contracts.contract_meta import ContractRecord
from rag_wright.contracts.property import FOLIO_SUBJECT_IRI, ClausePropertyRecord
from rag_wright.store.arcadedb import (
    CLAUSE_TYPE,
    CONTRACT_TYPE,
    PROPERTY_EDGE_TYPE,
    PROPVALUE_TYPE,
    _DIM_EDGE_STR,
    _edge_predicate_iri,
    _property_value_key,
)
from rag_wright.store.seam import KgEdge, KgNode


class ContractKGStore:
    """Typed clause-KG + contract-metadata writes over a generic `Store`."""

    def __init__(self, store) -> None:
        self._store = store

    def _already_written(self, clause_id: str) -> bool:
        """Content-hash gate (FR-S.2): a committed `clause_id` embeds the content hash, so identical content ->
        identical assertions -- skip (and the plain edge creates therefore run at most once per clause)."""
        return bool(self._store.kg_read(CLAUSE_TYPE, fields=["clause_id"], where={"clause_id": clause_id}, limit=1))

    def write_clause_kg(self, record: ClausePropertyRecord) -> None:
        """KG-3 (ADR-0033): the Clause node + one shared PropertyValue node per (dimension,value) + one TYPED edge
        per assertion (grounded with a predicate IRI + provenance). Idempotent by the content-hash gate."""
        if self._already_written(record.clause_id):
            return
        functions = [{"function": f.function, "confidence": f.confidence.value} for f in record.functions]
        nodes = [KgNode(CLAUSE_TYPE, "clause_id", {
            "clause_id": record.clause_id, "function": record.function, "folio_iri": record.folio_iri,
            "span_id": record.span_id, "functions": functions})]
        edges = []
        for a in record.assertions:
            edge_type = _DIM_EDGE_STR[a.dimension.value]
            key = _property_value_key(a.dimension.value, a.value)
            nodes.append(KgNode(PROPVALUE_TYPE, "value_key", {
                "value_key": key, "dimension": a.dimension.value, "value": a.value,
                "folio_iri": FOLIO_SUBJECT_IRI.get(a.value, "")}))
            edges.append(KgEdge(edge_type, CLAUSE_TYPE, "clause_id", record.clause_id, PROPVALUE_TYPE, "value_key",
                                key, {
                                    "dimension": a.dimension.value, "predicate_iri": _edge_predicate_iri(edge_type),
                                    "confidence": a.confidence.value, "span_id": a.span_id,
                                    "chunk_id": str(a.provenance.chunk_id),
                                    "source_doc_id": a.provenance.source_doc_id}))
        self._store.kg_write(nodes, edges)

    def write_property_graph(self, record: ClausePropertyRecord) -> None:
        """Legacy flat property graph (ADR-0025/0026; superseded by `write_clause_kg`): the Clause node + shared
        value nodes + one generic `HasProperty` edge per assertion. Same content-hash gate."""
        if self._already_written(record.clause_id):
            return
        nodes = [KgNode(CLAUSE_TYPE, "clause_id", {
            "clause_id": record.clause_id, "function": record.function, "folio_iri": record.folio_iri})]
        edges = []
        for a in record.assertions:
            key = _property_value_key(a.dimension.value, a.value)
            nodes.append(KgNode(PROPVALUE_TYPE, "value_key", {
                "value_key": key, "dimension": a.dimension.value, "value": a.value,
                "folio_iri": FOLIO_SUBJECT_IRI.get(a.value, "")}))
            edges.append(KgEdge(PROPERTY_EDGE_TYPE, CLAUSE_TYPE, "clause_id", record.clause_id, PROPVALUE_TYPE,
                                "value_key", key, {
                                    "confidence": a.confidence.value, "span_id": a.span_id,
                                    "chunk_id": str(a.provenance.chunk_id),
                                    "source_doc_id": a.provenance.source_doc_id}))
        self._store.kg_write(nodes, edges)

    def patch_canonical_jurisdictions(self) -> dict[str, int]:
        """KG-5a (DD-1c): additively canonicalize the `jurisdiction` value nodes -- write a `canonical_value` on
        each (surface `value` untouched, kept for citation), deterministically (no LLM, no re-extraction). Fixes the
        retrieval-match loss from surface variants (England / England and Wales / English law). Idempotent; the
        `canonical_value` property is pack-declared. Returns {seen, canonicalized}."""
        from rag_wright.contracts.jurisdiction import canonicalize_jurisdiction

        rows = self._store.kg_read(PROPVALUE_TYPE, fields=["value_key", "value"],
                                   where={"dimension": "jurisdiction"})
        nodes = []
        for r in rows:
            canon = canonicalize_jurisdiction(r["value"])
            if canon:
                nodes.append(KgNode(PROPVALUE_TYPE, "value_key",
                                    {"value_key": r["value_key"], "canonical_value": canon}))
        if nodes:
            self._store.kg_write(nodes)
        return {"seen": len(rows), "canonicalized": len(nodes)}

    def upsert_contract(self, record: ContractRecord) -> None:
        """CU-B3: upsert a contract's metadata by `contract_id`. `parties` -> native list (the store json-encodes
        the `parties_json` STRING column); `page_count` may be None (-> null)."""
        self._store.kg_write([KgNode(CONTRACT_TYPE, "contract_id", {
            "contract_id": record.contract_id, "name": record.name, "agreement_type": record.agreement_type,
            "parties_json": list(record.parties), "agreement_date": record.agreement_date,
            "effective_date": record.effective_date, "source_doc_id": record.source_doc_id,
            "content_hash": record.content_hash, "page_count": record.page_count})])
