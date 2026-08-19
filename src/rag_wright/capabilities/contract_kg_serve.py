"""KG-4 (FR-Q, ADR-0033): Leg A -- intra-contract scoped-query serving over the typed contract KG.

This is the structured/relational/cited backend the classification-only front door (`highlight_serve`)
couldn't give. Scoped to one `contract_id` (a clause_id is `<contract_id>:<index>:<hash>`, so a contract's
clauses are one key range), it answers questions over the typed KG that a clause-type filter can't:

- **disambiguate** same-type clauses by a property ("the *mutual* cap clause"; "the indemnity that covers
  *fraud*") -- `disambiguate`;
- **aggregate** across the contract ("every clause that waives *consequential* damages"; "all *governed_by*
  facts") -- `aggregate_by_property`, `clauses_of_function`;
- serve the whole **per-contract KG** (each clause + its typed properties) -- `contract_clause_index`.

Every answer is CITED: each clause carries its `clause_id` and each property its edge provenance
(`confidence` EXTRACTED/INFERRED/AMBIGUOUS + `span_id`, whose doc offsets live on the span) -- no claim
without a citation (FR-Q.6). The store is duck-typed (the three `*_contract*` / `clauses_with_property`
readbacks) so this layer is unit-tested with no live ArcadeDB.

Out of scope here (need layers not in the clause KG): party<->clause ROLE questions (the Party/PARTY_TO
layer, Leg C) and cross-clause REFERENCES (not extracted). Noted for a later pass.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class CitedProperty(BaseModel):
    """One typed property of a clause, with its edge provenance (FR-Q.6)."""

    dimension: str
    value: str
    edge_type: str
    predicate_iri: str = ""
    confidence: str = ""
    span_id: str = ""


class CitedClause(BaseModel):
    """A clause in a contract with its typed properties, each cited."""

    contract_id: str
    clause_id: str
    function: str
    folio_iri: str = ""
    span_id: str = ""  # the clause's operative span (1:1); rehydrate a property-less clause from THIS, reliably
    exception_of: str = ""  # ADR-0044: if set, this clause is an INFERRED carve-out/exception to that cap clause_id
    properties: list[CitedProperty] = []


@runtime_checkable
class _KGStore(Protocol):
    def clauses_in_contract(self, contract_id: str) -> list[dict]: ...
    def contract_clause_kg(self, contract_id: str) -> list[dict]: ...
    def clauses_with_property(self, contract_id: str, dimension: str, value: str) -> list[dict]: ...
    def all_spans_by_contract(self, contract_id: str) -> list[dict]: ...  # 0006-D: untyped-span fallback


def _contract_of(clause_id: str) -> str:
    return clause_id.split(":", 1)[0]


def _prop(row: dict) -> CitedProperty:
    return CitedProperty(
        dimension=row.get("dimension", ""), value=row.get("value", ""),
        edge_type=row.get("edge_type", ""), predicate_iri=row.get("predicate_iri") or "",
        confidence=row.get("confidence") or "", span_id=row.get("span_id") or "",
    )


def contract_clause_index(
    store: _KGStore, contract_id: str, *, include_untyped: bool = False
) -> list[CitedClause]:
    """The full per-contract KG: every clause (incl. property-less ones) with its typed properties, cited.

    `include_untyped` (issue 0006-D): also serve the spans the classifier left UNTYPED (`function == NONE`),
    which produce no Clause-KG node. Without this, a clause the classifier misses (e.g. a renewal clause it can't
    map to the taxonomy) is ABSENT from what Leg-A serves -> a silent recall hole ("not_found" on a clause that
    plainly exists). With it, an untyped span is served as a bare `CitedClause` (no properties), rehydrated from
    its own `span_id` -- so recall is decoupled from classification (the ADR-0047 whole-index posture)."""
    by_clause: dict[str, list[CitedProperty]] = {}
    for row in store.contract_clause_kg(contract_id):
        if not row.get("dimension"):  # a non-property clause edge (e.g. IsExceptionTo, ADR-0044) -> not a fact
            continue
        by_clause.setdefault(row["clause_id"], []).append(_prop(row))
    index = []
    for c in store.clauses_in_contract(contract_id):
        cid = c["clause_id"]
        index.append(CitedClause(
            contract_id=_contract_of(cid), clause_id=cid, function=c.get("function", ""),
            folio_iri=c.get("folio_iri") or "", span_id=c.get("span_id") or "",
            properties=by_clause.get(cid, []),
        ))
    if include_untyped:
        covered = {c.span_id for c in index if c.span_id}  # spans a typed clause already carries
        for row in store.all_spans_by_contract(contract_id):
            sid = row.get("span_id")
            if sid and sid not in covered and (row.get("text") or "").strip():
                index.append(CitedClause(
                    contract_id=contract_id, clause_id=sid, function=row.get("function") or "NONE",
                    span_id=sid, properties=[]))  # bare; rehydrate_clause_texts fetches its text by function+span
    return index


def clauses_of_function(store: _KGStore, contract_id: str, function: str) -> list[CitedClause]:
    """Aggregation by clause type: all clauses of `function` in the contract, cited with their properties."""
    return [c for c in contract_clause_index(store, contract_id) if c.function == function]


def disambiguate(
    store: _KGStore, contract_id: str, function: str, dimension: str, value: str
) -> list[CitedClause]:
    """The same-type clauses in the contract that assert (dimension, value) -- e.g. among several
    Cap-on-Liability clauses, the *mutual* one. The scoped-query answer classification can't give."""
    matched = {r["clause_id"] for r in store.clauses_with_property(contract_id, dimension, value)}
    return [c for c in clauses_of_function(store, contract_id, function) if c.clause_id in matched]


def aggregate_by_property(
    store: _KGStore, contract_id: str, dimension: str, value: str
) -> list[CitedClause]:
    """Every clause in the contract asserting (dimension, value), regardless of type -- e.g. all clauses
    that waive consequential damages, or every mutual obligation."""
    matched = {r["clause_id"] for r in store.clauses_with_property(contract_id, dimension, value)}
    by_id = {c.clause_id: c for c in contract_clause_index(store, contract_id)}
    return [by_id[cid] for cid in matched if cid in by_id]


def grounded_only(clauses: list[CitedClause]) -> list[CitedClause]:
    """Drop the AMBIGUOUS (grounding-judge-flagged) properties -- the high-precision view for a citation UI.
    Returns copies keeping only EXTRACTED/INFERRED properties."""
    return [
        c.model_copy(update={"properties": [p for p in c.properties if p.confidence != "AMBIGUOUS"]})
        for c in clauses
    ]


def register_intra_document_scoped_query(registry) -> None:
    """CAP-REG-2: register `intra_document_scoped_query` (function; intra-contract scoped KG serving)."""
    registry.register(
        "intra_document_scoped_query",
        contract=CitedClause,
        kind="function",
        display_name="Intra-document scoped query",
    )


def register_clause_disambiguation(registry) -> None:
    """CAP-REG-2: register `clause_disambiguation` (function; disambiguation by typed property)."""
    registry.register(
        "clause_disambiguation",
        contract=CitedClause,
        kind="function",
        display_name="Clause disambiguation",
    )
