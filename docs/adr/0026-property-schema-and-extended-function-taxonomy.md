# ADR-0026: Demand-driven property schema + extended function taxonomy (T57)

Status: accepted (2026-07-23)
Relates to: ADR-0025 (the function/property retrieval pivot this implements), ADR-0002 (the `ClauseCategory`
extraction ontology this deliberately does NOT reopen).

## Context

ADR-0025 pivoted retrieval to FUNCTION (clause type) + PROPERTY (qualifier). T57 defines the PROPERTY layer.
The schema was derived not from an ontology but from the demand side: the 57 ACORD test queries. 74% (42/57)
are FUNCTION+PROPERTY concentrated in two families (Limitation of Liability 28, Indemnification 14); the rest
are near-pure FUNCTION. The properties resolve into a small set of cross-cutting dimensions plus function-
specific ones.

Two questions had to be settled before encoding the contract:
1. **Adopt an external legal ontology (FOLIO / FIBO)?** Verified against the live FOLIO API and FIBO RDF:
   FOLIO is a rich clause-TYPE taxonomy (494-child `Contractual Clause` branch, IRIs for 15/16 of our types)
   but models NO clause PROPERTIES as first-class properties-with-values — only sparse, asymmetric, non-
   composing subclass tags (a `Mutual X` class but no `Unilateral X`; no favorability at all; its `Carve-out`
   label means corporate divestiture). FIBO's contract module is a generic + financial-instrument scaffold
   with no indemnification / liability-cap / carve-out concept — out of domain.
2. **Does the function taxonomy cover the queries?** No. The T56 classifier's labels are the 41 CUAD
   `ClauseCategory`, which has no Indemnification class (25% of queries) and no distinct damages-waiver or
   warranty-disclaimer class (much of the Limitation-of-Liability family). Using ACORD's coarse 9 categories
   instead was rejected: its "Limitation of Liability" bucket lumps Cap and Insurance together, re-introducing
   the exact confusion T56 was built to eliminate.

## Decision

**Property schema (`contracts/property.py`).** A two-tier model: 6 cross-cutting dimensions (mutuality,
favorability, carve_out, covered_subject, covered_parties, party_asymmetry) + 13 function-specific
(cap_basis/quantum, damage_type, warranty_scope, claim_scope, procedural, jurisdiction, law_multiplicity,
ip_ownership, nonsolicit_target, temporal_bound, renewal_mechanism, notice_period). Each property is a graph
fact — `PropertyAssertion` extends `GraphFact` (FR-S.4 provenance + confidence) and cites the operative span
(`span_id`). 15 dimensions carry a **closed** controlled vocabulary; a value outside it is admissible ONLY as
an AMBIGUOUS assertion (the `other` escape), so the extractor and the query-decomposer share one vocabulary.
Cap quantum is a light open scalar, not a structured money object (SPEC §8: the graph is the relationship
layer only). `ClausePropertyRecord` anchors every assertion to its clause (FR-Q.6).

**Extended function taxonomy (`contracts/function.py`).** `FUNCTION_LABELS` = the 41 CUAD categories (by
value, no drift) + 3 extensions (Indemnification, Indirect/Consequential Damages Waiver, Warranty Disclaimer)
= 44 classes; the T56 classifier is retrained over these (T60). This is kept **distinct** from `ClauseCategory`:
function routing is a retrieval concern, clause-fact extraction is a graph concern, so extending it does not
reopen the ADR-0002 extraction ontology even though 41 labels coincide by value.

**FOLIO as naming alignment only.** Store a FOLIO IRI as an attribute on clause-TYPE nodes (15/16 aligned;
Joint IP Ownership and Revenue/Profit Sharing have no clean FOLIO home → native) and on the 4 carve-out
subjects that have standalone concept IRIs. No OWL/RDF import, no reasoner, no schema shape from FOLIO. FIBO is
not used.

## Consequences

- The property layer is demand-bounded (models exactly what the queries discriminate on), keeping the extractor
  target tight and reliable and honoring the KISS standing rule against speculative abstraction.
- The function leg now covers all 57 queries once T60 retrains the classifier; that retrain needs training
  labels for the 3 new classes (no CUAD spans exist for them) — sourced by an LLM-labeled bootstrap over CUAD
  contracts' indemnification/waiver/disclaimer text plus ACORD graded pairs, behind its own sub-gate.
- FOLIO alignment gives interoperability at the type level for free while conceding that the property level is
  ours to define; a future non-legal corpus swaps FOLIO for that domain's ontology (or none) without touching
  the property model.
- T57 is delivered in slices: T57a (this contract) → T60 (extend + retrain) → T57b (extractor) → T57c (graph
  population), each behind the working-loop gate.

## References

- The 57-query decomposition and FOLIO/FIBO coverage findings (session 2026-07-23).
- `contracts/function.py`, `contracts/property.py`; tests `tests/contracts/test_function.py`,
  `tests/contracts/test_property.py`.
