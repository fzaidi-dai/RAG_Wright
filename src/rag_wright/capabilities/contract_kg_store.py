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
    IS_EXCEPTION_TO_EDGE_TYPE,
    PROPERTY_EDGE_TYPE,
    PROPVALUE_TYPE,
    TYPED_PROPERTY_EDGE_TYPES,
    _DIM_EDGE_STR,
    _edge_predicate_iri,
    _property_value_key,
)
from rag_wright.store.seam import NOT_NULL, KgEdge, KgNode


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

    # --- EP-REF-1a-ii: the typed clause-KG edge-traversal READS, over the generic `kg_edges` primitive. These
    #     moved OFF the engine store (which must hold no domain traversal, ADR-0117); the store keeps only the
    #     generic kg_edges/kg_read. Shapes are byte-for-byte the old store methods' (acceptance: identical reads).

    @staticmethod
    def _bounds(contract_id: str) -> tuple[str, str]:
        return contract_id + ":", contract_id + ";"  # clause_ids are `<contract>:<idx>:<hash>`

    def clause_typed_edges(self, clause_id: str) -> list[dict]:
        """The clause's typed property edges: edge type, dimension, value, predicate IRI, and provenance."""
        return self._store.kg_edges(
            CLAUSE_TYPE, where={"clause_id": clause_id}, direction="out",
            select={"edge_type": "e.@type", "predicate_iri": "e.predicate_iri", "dimension": "v.dimension",
                    "value": "v.value", "folio_iri": "v.folio_iri", "confidence": "e.confidence",
                    "span_id": "e.span_id"})

    def contract_clause_kg(self, contract_id: str) -> list[dict]:
        """The per-contract typed subgraph: one row per typed (clause -> value) edge, with the clause function,
        edge type/dimension/value/predicate IRI, and provenance. Only PROPERTY edges (which carry a `dimension`);
        the `dimension IS NOT NULL` guard excludes clause->clause edges (IsExceptionTo, ADR-0044)."""
        lo, hi = self._bounds(contract_id)
        return self._store.kg_edges(
            CLAUSE_TYPE, key_range=("clause_id", lo, hi), direction="out", edge_where={"dimension": NOT_NULL},
            select={"clause_id": "c.clause_id", "function": "c.function", "edge_type": "e.@type",
                    "dimension": "e.dimension", "value": "v.value", "folio_iri": "v.folio_iri",
                    "predicate_iri": "e.predicate_iri", "confidence": "e.confidence", "span_id": "e.span_id"})

    def clauses_with_property(self, contract_id: str, dimension: str, value: str) -> list[dict]:
        """The clauses in one contract that assert (dimension, value) -- clause_id + function + edge provenance."""
        lo, hi = self._bounds(contract_id)
        return self._store.kg_edges(
            CLAUSE_TYPE, key_range=("clause_id", lo, hi), direction="out",
            edge_where={"dimension": dimension}, target_where={"value": value},
            select={"clause_id": "c.clause_id", "function": "c.function", "edge_type": "e.@type",
                    "confidence": "e.confidence", "span_id": "e.span_id"})

    def exceptions_of_clause(self, cap_clause_id: str) -> list[dict]:
        """The exception/carve-out clauses linked to a Cap clause (`IsExceptionTo` in-edges, ADR-0044): the SOURCE
        clauses reached across the incoming edge. Rows: {clause_id, function, span_id}."""
        return self._store.kg_edges(
            CLAUSE_TYPE, where={"clause_id": cap_clause_id}, direction="in",
            edge_type=IS_EXCEPTION_TO_EDGE_TYPE,
            select={"clause_id": "v.clause_id", "function": "v.function", "span_id": "v.span_id"})

    def span_properties(self, span_ids: list[str]) -> dict[str, set[tuple[str, str]]]:
        """The typed (dimension, value) assertions on each span, joined via the ADR-0025 `edge.span_id`. Scans each
        typed edge type once (IN-filtered), so round-trips are bounded by the edge-type count, not the pool size.
        Returns {span_id: {(dimension, value)}} (every input span present, empty if none)."""
        out: dict[str, set[tuple[str, str]]] = {s: set() for s in span_ids}
        if not span_ids:
            return out
        for edge_type in TYPED_PROPERTY_EDGE_TYPES:
            for r in self._store.kg_edges(
                edge_type=edge_type, edge_where={"span_id": span_ids},
                select={"span_id": "span_id", "dimension": "dimension", "value": "inV().value"}):
                sid, dim, val = r.get("span_id"), r.get("dimension"), r.get("value")
                if sid in out and dim and val is not None:
                    out[sid].add((str(dim), str(val)))
        return out

    def clauses_in_contract(self, contract_id: str) -> list[dict]:
        """Every clause in one contract (a NODE read, delegated to the generic store -- it is a `kg_read`-
        relocatable follow-up, not an edge traversal). Kept here so this extension is the complete `_KGStore`
        reader the Leg-A serving (`contract_kg_serve`) needs."""
        return self._store.clauses_in_contract(contract_id)
