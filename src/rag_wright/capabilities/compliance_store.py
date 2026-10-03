"""ADR-0117 DD-1b: the compliance domain's store extension.

Composes a generic `Store` (`kg_write`/`kg_read`) to persist + read the Requirement KG, so the engine store imports
no compliance contract. A new domain writes its own typed records the same way -- the pattern, not the schema, is
what the engine provides. The `Requirement` vertex + its property storage types are schema-declared (ADR-0067 P5b),
so `kg_write` encodes each field (incl. `pages` as a native array, `bbox`/`applicability_json` as JSON strings)."""
from __future__ import annotations

from typing import Iterable, Optional

from rag_wright.capabilities.highlight_serve import _decode_bbox
from rag_wright.contracts.compliance import Requirement, RequirementLocation
from rag_wright.store.arcadedb import REQUIREMENT_TYPE
from rag_wright.store.seam import KgNode


class ComplianceStore:
    """Requirement-KG writes + the compliance READ facade over a generic `Store` (EP-REF-1b). The reads shape
    the stored `Requirement` rows into the domain answers a product serves (what EP-SEAM-3 lifts), so the engine
    store stays generic and a new domain supplies its own facade the same way."""

    def __init__(self, store) -> None:
        self._store = store

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

    # --- EP-REF-1b: the compliance READ facade (the reference versions the product seam lifts) ---------

    def requirements_for(self, source: str) -> list[dict]:
        """The curated requirement rows filed under one policy `source` -- the proof an ingest landed. DB-scoped
        (`all_requirements(sources=[source])`), so a multi-policy store never fetches the rows outside scope."""
        return self._store.all_requirements(sources=[source])

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
            for r in self._store.all_requirements(sources=[source])
        ]

    def curated_requirement_count(self, sources: Optional[list[str]] = None) -> int:
        """How many requirements are IN SCOPE -- the denominator of an honest coverage statement (consulted N of
        curated M). `sources=None` counts store-wide; a list scopes it (DB-side), `[]` is zero."""
        return len(self._store.all_requirements(sources=sources))

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
