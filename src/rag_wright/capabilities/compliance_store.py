"""ADR-0117 DD-1b: the compliance domain's store extension.

Composes a generic `Store` (`kg_write`/`kg_read`) to persist + read the Requirement KG, so the engine store imports
no compliance contract. A new domain writes its own typed records the same way -- the pattern, not the schema, is
what the engine provides. The `Requirement` vertex + its property storage types are schema-declared (ADR-0067 P5b),
so `kg_write` encodes each field (incl. `pages` as a native array, `bbox`/`applicability_json` as JSON strings)."""
from __future__ import annotations

from typing import Iterable

from rag_wright.contracts.compliance import Requirement
from rag_wright.store.arcadedb import REQUIREMENT_TYPE
from rag_wright.store.seam import KgNode


class ComplianceStore:
    """Requirement-KG writes over a generic `Store`."""

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
