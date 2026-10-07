"""ADR-0117 DD-1b: the contract domain's store extension.

Composes a generic `Store` (`kg_write`/`kg_read`) to write the typed clause KG (KG-3), the legacy flat property
graph, and contract metadata -- so the engine store imports no contract contract. The clause/value/contract vertex
types + the dimension->edge map + predicate IRIs are pack-authoritative (`contract_bridge.ttl`, ADR-0067 P5a/P5b);
`kg_write` encodes each field per its declared storage type (e.g. `functions` as a JSON string). Domain-native
values in; the store owns all wire encoding."""
from __future__ import annotations

from typing import Optional

from rag_wright.packs.contracts.capabilities.highlight_serve import _decode_bbox
from rag_wright.packs.contracts.schemas.contract_meta import ContractRecord
from rag_wright.packs.contracts.schemas.highlight import SpanLocation
from rag_wright.packs.contracts.schemas.property import FOLIO_SUBJECT_IRI, ClausePropertyRecord
from rag_wright.store.arcadedb import SPAN_TYPE, _sql_str
from rag_wright.contracts.provenance import ConfidenceTag
from rag_wright.packs.contracts.ontology.loader import load_typed_edges, reference_pack_ttl
from rag_wright.packs.contracts.ontology.contract_taxonomy import AFFILIATE_OF, CONTRACTS_WITH  # DD-5 contract edge names
from rag_wright.store.seam import NOT_NULL, KgEdge, KgNode


# KG-3 (ADR-0033) / ADR-0067 P5a: the reference pack's TYPED property-edge layer -- each property dimension maps to
# its sanctioned typed edge (dimension -> KG edge type) + a predicate IRI, AUTHORITATIVE in contract_bridge.ttl
# (`cbr:kgEdge` / `cbr:KgEdgeType`). Moved here from the generic store (ING-8b): a contract-pack concept.
_DIM_EDGE_STR, _EDGE_PREDICATE_IRI = load_typed_edges(reference_pack_ttl())
TYPED_PROPERTY_EDGE_TYPES: tuple[str, ...] = tuple(sorted(set(_DIM_EDGE_STR.values())))


def _edge_predicate_iri(edge_type: str) -> str:
    """The predicate IRI stamped on a typed edge (ODRL for the deontic edges, the bridge IRI otherwise)."""
    return _EDGE_PREDICATE_IRI[edge_type]


def _stale_property_statements(span_ids: list[str]) -> list[str]:
    """ADR-0048 Phase A mark-stale (pure): one `UPDATE ... SET confidence=AMBIGUOUS WHERE span_id IN (...) AND
    confidence <> AMBIGUOUS` per typed property edge type, for the given spans. Separated from the DB call so the
    SQL is unit-tested with no store. Empty span list -> no statements (the caller no-ops)."""
    if not span_ids:
        return []
    id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
    amb = _sql_str(ConfidenceTag.AMBIGUOUS.value)
    return [
        f"UPDATE {edge_type} SET confidence = {amb} WHERE span_id IN {id_list} AND confidence <> {amb}"
        for edge_type in TYPED_PROPERTY_EDGE_TYPES
    ]


# ING-8e: the contracts pack's KG types (declared in contract_bridge.ttl), moved off the generic store.
# FR-R (ADR-0025/0026) property graph: clause node -> typed property edge -> shared property-value node.
CLAUSE_TYPE = "Clause"
PROPVALUE_TYPE = "PropertyValue"
PROPERTY_EDGE_TYPE = "HasProperty"  # legacy flat edge (ADR-0025/0026); superseded by the KG-3 typed edges
CONTRACT_TYPE = "Contract"  # CU-B3 (ADR-0029): contract-level metadata (the CUAD document lookup unit)
IS_EXCEPTION_TO_EDGE_TYPE = "IsExceptionTo"  # ADR-0044: exception clause (Uncapped) -> the Cap clause it excepts


def _property_value_key(dimension: str, value: str) -> str:
    """The shared `PropertyValue` node identity: the canonical (dimension, value). The controlled
    vocabulary is already canonical, so dedup across clauses is a deterministic upsert by this key."""
    return f"{dimension}:{value}"


def _ensure_reference_schema(store) -> None:
    """ING-8a: the engine's default schema is neutral, so the reference contract pack ensures ITS schema (Clause,
    PropertyValue, Contract, the typed edges) on the store it uses -- once per store object."""
    ensure = getattr(store, "ensure_pack_schema", None)
    if not callable(ensure):
        return
    ttl = reference_pack_ttl()
    if ttl not in store.schema_packs():
        ensure(ttl)
        store.ensure_edge_types(TYPED_PROPERTY_EDGE_TYPES)  # the pack's own typed property edges (ING-8b)


class ContractKGStore:
    """Typed clause-KG + contract-metadata writes over a generic `Store`."""

    def __init__(self, store) -> None:
        self._store = store
        _ensure_reference_schema(store)

    def _already_written(self, clause_id: str) -> bool:
        """Content-hash gate (FR-S.2): a committed `clause_id` embeds the content hash, so identical content ->
        identical assertions -- skip (and the plain edge creates therefore run at most once per clause)."""
        return bool(self._store.kg_read(CLAUSE_TYPE, fields=["clause_id"], where={"clause_id": clause_id}, limit=1))

    def write_clause_kg(self, record: ClausePropertyRecord) -> None:
        """KG-3 (ADR-0033): the Clause node + one shared PropertyValue node per (dimension,value) + one TYPED edge
        per assertion (grounded with a predicate IRI + provenance). Idempotent by the content-hash gate."""
        if self._already_written(record.clause_id):
            return
        nodes, edges = clause_kg_graph(record)
        self._store.kg_write(nodes, edges)

    def already_written(self, clause_id: str) -> bool:
        """ING-4c: the content-hash gate `write_clause_kg` applies, for a writer that builds the graph itself."""
        return self._already_written(clause_id)

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
        from rag_wright.packs.contracts.schemas.jurisdiction import canonicalize_jurisdiction

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

    # --- moved from the generic store (ING-8b): the typed clause KG's counts / clear / mark-stale ----------

    def clause_kg_counts(self) -> dict[str, int]:
        """Counts for the typed KG (introspection/tests): clauses, shared value nodes, and the total of the
        typed property edges across all typed edge types."""
        clauses = self._store._query(f"SELECT count(*) AS n FROM {CLAUSE_TYPE}")
        values = self._store._query(f"SELECT count(*) AS n FROM {PROPVALUE_TYPE}")
        typed = 0
        present = self._store.type_names()  # a DB populated before a schema extension lacks the newer edge types
        for edge_type in TYPED_PROPERTY_EDGE_TYPES:
            if edge_type not in present:
                continue
            rows = self._store._query(f"SELECT count(*) AS n FROM {edge_type}")
            typed += int(rows[0]["n"]) if rows else 0
        return {
            "clauses": int(clauses[0]["n"]) if clauses else 0,
            "property_values": int(values[0]["n"]) if values else 0,
            "typed_edges": typed,
        }

    def clear_clause_kg(self) -> None:
        """Delete the typed clause KG (all typed edges + the legacy flat edge + Clause + PropertyValue),
        leaving the span index intact -- the KG-3 counterpart of `clear_property_graph` for a clean
        re-extraction into the typed schema. Edges first (UNSAFE bypasses the edge-safety check)."""
        present = self._store.type_names()  # skip edge types a pre-extension DB never created
        for edge_type in (*TYPED_PROPERTY_EDGE_TYPES, PROPERTY_EDGE_TYPE):
            if edge_type in present:
                self._store._command(f"DELETE FROM {edge_type} UNSAFE")
        self._store._command(f"DELETE FROM {CLAUSE_TYPE}")
        self._store._command(f"DELETE FROM {PROPVALUE_TYPE}")

    def mark_span_properties_ambiguous(self, span_ids: list[str]) -> int:
        """ADR-0048 Phase A mark-stale: set `confidence = AMBIGUOUS` on every typed property edge of these spans.
        A clause whose PRIMARY function flipped had its properties extracted for the OLD function, so they are
        stale until Phase B re-extraction -- downgraded (kept but flagged) exactly as the ADR-0040 judges do, so
        the soft-boost down-weights them meanwhile. Keyed by the ADR-0025 `edge.span_id`. Idempotent (skips
        already-AMBIGUOUS). Returns the number of edges downgraded (best-effort from the driver's row count)."""
        if not span_ids:
            return 0
        total = 0
        for stmt in _stale_property_statements(span_ids):
            res = self._store._command(stmt)
            if isinstance(res, list):
                for row in res:
                    if isinstance(row, dict) and "count" in row:
                        total += int(row["count"])
        return total

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
        return self._clauses_in_contract(contract_id)

    def all_spans_by_contract(self, contract_id: str) -> list[dict]:
        """Every span in one contract (delegated to the generic store). The Leg-A serving
        (`contract_clause_index(..., include_untyped=True)`) reads this so a span the classifier left untyped is
        still a candidate -- without it, serve raised AttributeError and intra_document_qa abstained on EVERY doc."""
        return self._store.all_spans_by_document(contract_id)

    # --- EP-REF-1b-ii: contract traversal + vocab (the reference reads EP-SEAM-3 lifts), over the entity-graph
    #     traversal primitive + the clause index. The CONTRACTS_WITH / AFFILIATE_OF naming is the DD-5 contract
    #     vocabulary over the generic (domain-free) graph_query.

    def party_counterparties(self, entity_id: str, *, max_hops: int = 1,
                             documents: Optional[list[str]] = None) -> list:
        """The parties this one has a CONTRACTS_WITH edge to, each citing the contract it came from. ONE hop by
        default -- two hops would return the counterparties OF the counterparties (parties this one has no
        agreement with), overstating exposure. `documents` scopes the traversal (every edge on a path must belong)."""
        from rag_wright.capabilities.graph_query import graph_query
        return list(graph_query(entity_id, store=self._store, relationship_type=CONTRACTS_WITH,
                                max_hops=max_hops, documents=documents).evidence)

    def party_affiliates(self, entity_id: str, *, documents: Optional[list[str]] = None) -> list:
        """The parties this one has an AFFILIATE_OF edge to (same corporate group). A SEPARATE traversal from
        counterparties on purpose: a parent and its subsidiary are two legal persons whose obligations must not be
        pooled, and the engine does not merge the entities -- the affiliate is its own node."""
        from rag_wright.capabilities.graph_query import graph_query
        return list(graph_query(entity_id, store=self._store, relationship_type=AFFILIATE_OF,
                                documents=documents).evidence)

    def contract_terms(self, contract_id: str) -> list:
        """The typed clauses of one contract -- the terms that create the exposure. The FULL view, NOT
        grounded-only: the high-precision view drops AMBIGUOUS properties, including the verbatim out-of-vocab
        values ADR-0102 kept, so an exposure/citation surface must read what the graph holds, not less (a
        precision filter is a per-caller UI choice, never baked into the facade)."""
        from rag_wright.packs.contracts.capabilities.contract_kg_serve import contract_clause_index
        return list(contract_clause_index(self, contract_id))

    # --- EP-REF-1b-iii: clause-type taxonomy (pure, from schemas/function.py) + span location assembly ---

    @staticmethod
    def canonical_clause_type(label: str) -> Optional[str]:
        """Map a user's clause label onto the engine's clause-type taxonomy, or `None` if it maps to nothing.
        Case-insensitive + alias-resolving ("Limitation of Liability" -> "Cap On Liability"), so a caller's label
        (e.g. a correction) is VALIDATED rather than taken on trust -- an unmappable label returns None, not a
        silent empty scope."""
        from rag_wright.packs.contracts.schemas.function import canonical_function
        return canonical_function(label)

    @staticmethod
    def clause_type_vocabulary() -> tuple[str, ...]:
        """Every clause type a sweep/correction UI can be scoped to (the engine's function-label vocabulary)."""
        from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS
        return tuple(FUNCTION_LABELS)

    def span_locations(self, contract_id: str) -> list[SpanLocation]:
        """Every span of one document with where it sits in the original (pages/bbox/offsets) + the clause ids
        extracted from it -- for a citation PREVIEW (a span-id-in-hand lookup, no query/model call). Each clause
        carries the `span_id` it came from (the only bridge between the clause-id and span-id spaces); a clause
        whose span is not in this document is dropped rather than inventing a location."""
        spans = self._store.all_spans_by_document(contract_id)
        present = {r["span_id"] for r in spans}
        clauses: dict[str, list[str]] = {}
        for c in self.clauses_in_contract(contract_id):
            sid = c.get("span_id") or ""
            if sid in present:
                clauses.setdefault(sid, []).append(str(c.get("clause_id") or ""))
        return [
            SpanLocation(
                span_id=r["span_id"], clause_ids=clauses.get(r["span_id"], []),
                pages=[int(p) for p in (r.get("pages") or [])], bbox=_decode_bbox(r.get("bbox")),
                doc_start=r.get("doc_start"), doc_end=r.get("doc_end"), text=r.get("text") or "",
            )
            for r in spans
        ]


    # --- ING-8e: contract reads/writes moved off the generic store -------------------------------------

    def contract_by_id(self, contract_id: str) -> dict | None:
        """CU-B3: look up a contract's metadata by id (the row, or None if absent)."""
        rows = self._store.kg_read(CONTRACT_TYPE, fields=[
            "contract_id", "name", "agreement_type", "parties_json", "agreement_date", "effective_date",
            "source_doc_id", "content_hash", "page_count"], where={"contract_id": contract_id})
        return rows[0] if rows else None

    def all_contracts(self) -> list[dict]:
        """Every contract id in the store (e.g. for a corpus-wide backfill pass)."""
        return self._store._query(f"SELECT contract_id FROM {CONTRACT_TYPE}")

    def _contract_bounds(self, contract_id: str) -> tuple[str, str]:
        return _sql_str(contract_id + ":"), _sql_str(contract_id + ";")

    def _clauses_in_contract(self, contract_id: str) -> list[dict]:
        """Every clause in one contract (Leg-A scope): clause_id, function, folio_iri -- including clauses
        with no typed properties (still queryable by type)."""
        lo, hi = self._contract_bounds(contract_id)
        return self._store._query(
            f"SELECT clause_id, function, folio_iri, span_id FROM {CLAUSE_TYPE} "
            f"WHERE clause_id >= {lo} AND clause_id < {hi} ORDER BY clause_id"
        )

    def clause_positions(self, functions: list[str]) -> list[dict]:
        """Clauses of the given functions with their operative-span DOCUMENT offsets (via the clause-level
        span_id, ADR-0042), for proximity-based exception linking. Rows: {clause_id, function, contract_id,
        doc_start, doc_end}. A clause with no resolvable span (legacy/unbackfilled) is skipped."""
        if not functions:
            return []
        fn_list = "[" + ",".join(_sql_str(f) for f in functions) + "]"
        clauses = self._store._query(
            f"SELECT clause_id, function, span_id FROM {CLAUSE_TYPE} WHERE function IN {fn_list}")
        span_ids = [c["span_id"] for c in clauses if c.get("span_id")]
        if not span_ids:
            return []
        id_list = "[" + ",".join(_sql_str(s) for s in span_ids) + "]"
        spans = self._store._query(
            f"SELECT span_id, doc_start, doc_end, document_id FROM {SPAN_TYPE} WHERE span_id IN {id_list}")
        by_span = {s["span_id"]: s for s in spans}
        out: list[dict] = []
        for c in clauses:
            s = by_span.get(c.get("span_id"))
            if s is None:
                continue
            out.append({"clause_id": c["clause_id"], "function": c["function"],
                        "contract_id": s.get("document_id"), "doc_start": s.get("doc_start"),
                        "doc_end": s.get("doc_end")})
        return out

    def write_clause_exception_links(self, links: list) -> None:
        """ADR-0044: write the `IsExceptionTo` edges (exception/Uncapped clause -> the Cap clause it excepts).
        Idempotent: clears the existing IsExceptionTo layer first, so re-linking is safe and re-derivable. The
        edge carries the INFERRED confidence (a derived, reasoned link, FR-S.4). One transaction."""
        statements = (
            [f"DELETE FROM {IS_EXCEPTION_TO_EDGE_TYPE} UNSAFE"]
            if IS_EXCEPTION_TO_EDGE_TYPE in self._store.type_names() else [])
        for link in links:
            statements.append(
                f"CREATE EDGE {IS_EXCEPTION_TO_EDGE_TYPE}"
                f" FROM (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {_sql_str(link.exception_clause_id)})"
                f" TO (SELECT FROM {CLAUSE_TYPE} WHERE clause_id = {_sql_str(link.cap_clause_id)})"
                f" SET confidence = {_sql_str(link.confidence.value)}"
            )
        if statements:
            self._db.execute_transaction(statements)

    def clear_property_graph(self) -> None:
        """Delete all property-graph records (Clause / PropertyValue / HasProperty) while LEAVING the span
        index intact -- so a property re-extraction can start from scratch without re-embedding (T58 resume
        control). Edges first (UNSAFE bypasses the edge-safety check this dialect requires), then vertices."""
        self._store._command(f"DELETE FROM {PROPERTY_EDGE_TYPE} UNSAFE")
        self._store._command(f"DELETE FROM {CLAUSE_TYPE}")
        self._store._command(f"DELETE FROM {PROPVALUE_TYPE}")

    def property_graph_counts(self) -> dict[str, int]:
        """Counts for introspection/tests: clauses, shared property-value nodes, and property edges."""
        clauses = self._store._query(f"SELECT count(*) AS n FROM {CLAUSE_TYPE}")
        values = self._store._query(f"SELECT count(*) AS n FROM {PROPVALUE_TYPE}")
        edges = self._store._query(f"SELECT count(*) AS n FROM {PROPERTY_EDGE_TYPE}")
        return {
            "clauses": int(clauses[0]["n"]) if clauses else 0,
            "property_values": int(values[0]["n"]) if values else 0,
            "property_edges": int(edges[0]["n"]) if edges else 0,
        }

    def clause_property_values(self, clause_id: str) -> list[dict]:
        """The property values a clause asserts, each with the edge's provenance (dimension, value,
        confidence, span_id) -- the readback for tests and the shape T58's query builds on."""
        q = (
            "MATCH {type: " + CLAUSE_TYPE + ", as: c, where: (clause_id = " + _sql_str(clause_id) + ")}"
            ".outE('" + PROPERTY_EDGE_TYPE + "'){as: e}.inV(){as: v}"
            " RETURN v.dimension AS dimension, v.value AS value, e.confidence AS confidence,"
            " e.span_id AS span_id"
        )
        return self._store._query(q)


def clause_kg_graph(record: ClausePropertyRecord) -> tuple[list[KgNode], list[KgEdge]]:
    """KG-3 (ADR-0033): the Clause node + one shared PropertyValue node per (dimension, value) + one TYPED edge per
    assertion (grounded with a predicate IRI + provenance) -- exactly what `write_clause_kg` writes. ING-4c: factored
    out so the reference extractor can return a clause's graph as a `UnitExtraction`."""
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
    return nodes, edges
