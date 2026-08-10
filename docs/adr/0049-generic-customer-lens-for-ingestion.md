# ADR-0049: The generic-customer lens — evaluate every ingestion/KG move by what a new customer inherits

## Context

RAG_Wright is the capability half of a **generic SME contract-intelligence product** (see
`docs/product/contracts_product_roadmap.md`): we ship an ontology, KG-construction capabilities, query pipelines
(the MCP Tier-1 capabilities), and the orchestrator/background agents that GraphWright compiles. **CUAD and ACORD
are development and validation corpora — a stand-in customer — not the product.**

We drifted from this. The ADR-0048 arc (fix mislabeled clause functions) culminated in a full LLM reclassify WRITE
over the CUAD/ACORD KG, an independent audit, a selective revert, and a proposed exhaustive judge over a ~4,088
"tail" of small-transition flips. All of that optimizes *one specific dev corpus's labels*. A brand-new customer
never inherits CUAD's labels — their KG is built by our ontology-driven extraction over *their* documents. So the
tail-polishing had ~zero generic product value.

Two technical findings sharpen the point:
- **Extraction is function-agnostic** (ADR-0048 Phase-A addendum): `extract_clause(text)` fills the ontology's
  Clause template from text; the function label only gates applicability afterward (`symbolic_validate`). So
  extraction quality is a function of **the ontology (template + value vocabularies + function→dimension
  applicability), not the labels**. Labels route and gate; the ontology is the substance.
- Therefore the highest-leverage generic move at ingestion time is **enriching the ontology / `.ttl`**, because it
  lifts *every* customer's KG at once, whereas relabeling a dev corpus lifts nothing downstream.

## Decision

Adopt a standing **generic-customer lens**: every proposed ingestion/KG-construction move is evaluated by one test
— *does a brand-new customer's `ingest → KG → query` benefit from it?* Concretely:

1. **The ontology (`.ttl`) is the master KNOWLEDGE lever** at ingestion: clause-type taxonomy, property dimensions,
   value vocabularies, the function→dimension applicability map, and FOLIO/ODRL grounding. Enriching it is generic
   and is the default place to invest.
2. **The extraction MECHANISM is the co-equal EXECUTION lever** (classifier, extractor recipe, the
   grounding/symbolic/semantic judges). It is also generic — e.g. the ADR-0048 enum-constrained classifier and the
   never-null-on-NONE ingestion invariant improve *any* customer's ingest. Keep it honest; it multiplies with the
   ontology.
3. **Dev-corpus label cleanup is validation, not a deliverable.** The reclassify/audit/revert machinery is retained
   as reusable QC/validation tooling (prove the classifier produces sane labels on real text), but a specific
   relabeled CUAD/ACORD KG is a dev artifact. We do NOT invest further in CUAD-specific label polishing (the
   ~4,088 tail is dropped as product work) unless it de-risks a generic mechanism.
4. **Source connectors (Drive/GCS/S3/zip) are ingestion plumbing, decoupled from ontology building.** They feed
   documents in; they have no bearing on how the ontology is built or updated. Sequenced with product use-cases
   later, not here.

This lens is standing: apply it to proposals going forward, and prefer generic ontology/mechanism levers over
corpus-specific tuning. CUAD/ACORD remain the measurement harness (how we *know* a generic change helped).

## Consequences

- The ADR-0048 Phase-A reclassify is reframed as a **validation** that the enum-constrained classifier works on
  real data; the CUAD label tail is not pursued further. Phase B (property re-extraction over the flipped delta)
  is likewise dev-corpus-specific and de-prioritized as product work.
- The durable product value banked this arc: the enum-constrained classifier, the never-null ingestion invariant,
  the richer 52-label taxonomy + fold map (a better clause-type vocabulary any customer inherits), and the
  audit-harness QC pattern.
- Next generic work targets the ontology: (1) complete the 8 new clause types' property-dimension applicability
  (move them out of `PERMISSIVE_FUNCTIONS` into `FUNCTION_APPLICABLE_DIMS`, adding dimensions/value-vocab as
  needed) — a direct `.ttl` enrichment; (2) a **dimension/property gap analysis** (the property analog of the
  taxonomy-gap analysis) to find facets the Clause template cannot express and add them — the deep master-lever
  enrichment. (3) Function-conditioned extraction (letting clause-type knowledge inform extraction) is kept as a
  future experiment, gated on measurement.
- Consistent with the two-halves boundary (ADR / CLAUDE.md): we enrich capabilities and the ontology here;
  orchestration and use-case workflows are GraphWright's, sequenced with the product roadmap later.
