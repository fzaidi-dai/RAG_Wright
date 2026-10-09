"""ADR-0117 DD-1b: the compliance domain's store extension.

Composes a generic `Store` (`kg_write`/`kg_read`) to persist + read the Requirement KG, so the engine store imports
no compliance contract. A new domain writes its own typed records the same way -- the pattern, not the schema, is
what the engine provides. The `Requirement` vertex + its property storage types are schema-declared (ADR-0067 P5b),
so `kg_write` encodes each field (incl. `pages` as a native array, `bbox`/`applicability_json` as JSON strings)."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from rag_wright.packs.contracts.capabilities.highlight_serve import _decode_bbox
from rag_wright.packs.compliance.schemas.compliance import Requirement, RequirementLocation
from rag_wright.api import KgNode

# ING-8e: the compliance pack's KG type, declared in its compliance_bridge.ttl (moved off the generic store).
REQUIREMENT_TYPE = "Requirement"  # CC-5 (compliance §13): a deontic regulatory rule
_COMPLIANCE_TTL = str(Path(__file__).resolve().parents[1] / "ontology" / "compliance_bridge.ttl")


class ComplianceStore:
    """Requirement-KG writes + the compliance READ facade over a generic `Store` (EP-REF-1b). The reads shape
    the stored `Requirement` rows into the domain answers a product serves (what EP-SEAM-3 lifts), so the engine
    store stays generic and a new domain supplies its own facade the same way."""

    def __init__(self, store) -> None:
        self._store = store
        self.ensure_compliance_schema()

    def write_requirements(self, requirements: Iterable[Requirement]) -> int:
        """Upsert `Requirement` nodes by `requirement_id` (idempotent -- a re-ingest is a no-op on unchanged rules),
        in one transaction. Domain-native values in; the store encodes per the declared storage type. Returns count."""
        nodes = [
            KgNode(REQUIREMENT_TYPE, "requirement_id", {
                "requirement_id": req.requirement_id,
                "source": req.source,
                "citation": req.citation,
                "deontic_type": req.deontic_type.value,
                "actor": req.actor,
                "requirement_text": req.requirement_text,
                "evidence_standard": req.evidence_standard or "",
                "severity": req.severity.value if req.severity else "",
                "applicability_json": [[c.dimension, c.value] for c in req.applicability_scope],
                "confidence": req.confidence.value,
                "pages": [int(p) for p in req.pages],
                "bbox": list(req.bbox) if req.bbox is not None else None,
            })
            for req in requirements
        ]
        self._store.kg_write(nodes)
        return len(nodes)

    # --- ING-8e: the Requirement schema + reads, moved off the generic store --------------------------

    def ensure_compliance_schema(self) -> None:
        """Create the compliance schema (the `Requirement` vertex type + its UNIQUE `requirement_id` index) from the
        pack's own `compliance_bridge.ttl` (ING-8e; was a generic-store method). Additive + idempotent, once per
        store object. Intended for a SEPARATE database (`ragwright_compliance`) so the contract KG stays clean."""
        ensure = getattr(self._store, "ensure_pack_schema", None)
        if callable(ensure) and _COMPLIANCE_TTL not in self._store.schema_packs():
            ensure(_COMPLIANCE_TTL)

    def all_requirements(self, sources: Optional[Iterable[str]] = None) -> list[dict]:
        """Stored `Requirement` rows (CC-6 loads these to match a claim's scope against applicability).

        `sources=None` returns every row (store-wide, unchanged). Issue 0007: when a list of policy `source`s is
        given, the filter is pushed into the QUERY (`WHERE source IN [...]`) so a store holding thousands of rows
        across many policies/tenants never fetches the ones outside the scope -- scale-ready, not an in-memory
        filter. An empty scope (`sources=[]`) returns `[]` without a query (scope-to-nothing; also avoids an
        invalid `IN []`)."""
        if sources is not None:
            sources = list(sources)
            if not sources:
                return []  # empty scope -> [] without a query
        return self._store.kg_read(REQUIREMENT_TYPE, fields=[
            "requirement_id", "source", "citation", "deontic_type", "actor", "requirement_text",
            "evidence_standard", "severity", "applicability_json", "confidence", "pages", "bbox"],
            where=({"source": sources} if sources is not None else None))

    def requirement_sources(self) -> set[str]:
        """Issue 0007: the DISTINCT set of policy `source`s present in the Requirement KG -- powers unknown-source
        validation (naming a policy that does not exist) WITHOUT loading any requirement rows. The Requirement type
        may not exist yet on a fresh DB -> empty set."""
        if REQUIREMENT_TYPE not in self._store.type_names():
            return set()
        rows = self._store.kg_read(REQUIREMENT_TYPE, distinct="source")
        return {r["source"] for r in rows if r.get("source")}

    def ingested_citations(self, source: str) -> set[str]:
        """COMP-ASYNC-1 resume (PROD-2 #2): the set of `citation`s that ALREADY have >=1 `Requirement` for `source`
        -- the compliance analogue of a present `Contract` node. A section in this set was successfully ingested
        (a FAILED or genuinely-empty section wrote 0 requirements, so it is absent and correctly re-runs). The
        Requirement type may not exist yet on a fresh DB -> empty set."""
        if REQUIREMENT_TYPE not in self._store.type_names():
            return set()
        rows = self._store.kg_read(REQUIREMENT_TYPE, where={"source": source}, distinct="citation")
        return {r["citation"] for r in rows if r.get("citation")}

    # --- EP-REF-1b: the compliance READ facade (the reference versions the product seam lifts) ---------

    def requirements_for(self, source: str) -> list[dict]:
        """The curated requirement rows filed under one policy `source` -- the proof an ingest landed. DB-scoped
        (`all_requirements(sources=[source])`), so a multi-policy store never fetches the rows outside scope."""
        return self.all_requirements(sources=[source])

    def requirement_locations(self, source: str) -> list[RequirementLocation]:
        """Where each requirement of one policy sits in its document (pages + optional bbox), for a citation
        preview. Reads defensively: a requirement curated before provenance landed (ADR-0107) has no pages/bbox,
        so `pages` -> [] and a malformed/absent `bbox` -> None (a page is still openable without a rectangle)."""
        return [
            RequirementLocation(
                requirement_id=str(r.get("requirement_id") or ""),
                citation=str(r.get("citation") or ""),
                pages=[int(p) for p in (r.get("pages") or [])],
                bbox=_decode_bbox(r.get("bbox")),
                text=str(r.get("requirement_text") or ""),
            )
            for r in self.all_requirements(sources=[source])
        ]

    def curated_requirement_count(self, sources: Optional[list[str]] = None) -> int:
        """How many requirements are IN SCOPE -- the denominator of an honest coverage statement (consulted N of
        curated M). `sources=None` counts store-wide; a list scopes it (DB-side), `[]` is zero."""
        return len(self.all_requirements(sources=sources))

    @staticmethod
    def policy_of_requirement(requirement_id: str) -> str:
        """The policy a requirement belongs to, from its id `<source>:<section>:<hash>`. Split on the FIRST colon
        -- a source name may legitimately contain dots/hyphens ("ISO.27001-2022"), which a last-colon split eats.
        Empty id -> ''. This prefix is the only link a `ComplianceFinding` carries back to its policy."""
        return requirement_id.split(":", 1)[0] if requirement_id else ""

    @staticmethod
    def gated_pairs(report) -> list[dict]:
        """The (assertion|document, rule) pairs the symbolic ACTOR gate skipped before any judge call -- honest
        coverage (ADR-0068): a shallow copy of `ComplianceReport.gated_pairs`. The ACTOR gate ONLY; top-k semantic
        narrowing also drops pairs and is NOT reported here, so `[]` does not mean every pair was evaluated."""
        return [dict(entry) for entry in (getattr(report, "gated_pairs", None) or [])]
