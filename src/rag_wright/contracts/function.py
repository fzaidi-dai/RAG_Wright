"""The retrieval FUNCTION taxonomy (T57, FR-C.6, ADR-0025).

The set of clause types the FUNCTION classifier (T56) routes on. It is a SUPERSET of the 41 CUAD
`ClauseCategory` (mirrored by value, so there is no drift) plus the three ACORD query families CUAD
has no class for: Indemnification (14/57 test queries), the indirect/consequential damages waiver,
and the warranty disclaimer (the bulk of the "Limitation of Liability" family beyond Cap/Uncapped).

This is kept DISTINCT from `ClauseCategory` on purpose. `ClauseCategory` is the graph EXTRACTION
ontology (ADR-0002): a closed vocabulary the knowledge graph conforms to, not reopened here. The
FUNCTION taxonomy is a RETRIEVAL concern (which clause type a span is routed under). The two serve
different layers even though 41 labels coincide by value, so extending function routing does not
reopen the extraction ontology.

`FUNCTION_LABELS` is the classifier's full label space and its retrain target (the T56 LegalBERT is
retrained over these 44 classes plus its own NONE sentinel; NONE is not a function type and is not
listed here).
"""

from __future__ import annotations

from enum import Enum

from rag_wright.contracts.ontology import ClauseCategory


class ExtendedFunction(str, Enum):
    """The ACORD query families the 41 CUAD `ClauseCategory` has no class for (T57). Values are the
    canonical function labels the classifier emits and the query-decomposer targets."""

    INDEMNIFICATION = "Indemnification"
    INDIRECT_DAMAGES_WAIVER = "Indirect/Consequential Damages Waiver"
    WARRANTY_DISCLAIMER = "Warranty Disclaimer"


# The classifier's full label space: the 41 CUAD categories (by value, no drift) then the 3
# extensions. Order is stable (CUAD order, then extension order) so a retrain's label<->id map is
# reproducible. NONE is the classifier's off-taxonomy sentinel, not a function type -> not listed.
FUNCTION_LABELS: tuple[str, ...] = tuple(
    [c.value for c in ClauseCategory] + [f.value for f in ExtendedFunction]
)
FUNCTION_LABEL_SET: frozenset[str] = frozenset(FUNCTION_LABELS)
