# ADR-0051: Schema bootstrap + feedback-driven ontology evolution (the living ontology)

Status: Proposed (design/strategy; forward-looking, product-gated)
Date: 2026-08-12
Related: ADR-0049 (generic-customer lens), ADR-0048 (nondestructive reclassify), ADR-0050 (async/lossless ingestion), ADR-0040 (neuro-symbolic judge), ADR-0033 (unified contract KG design)

## Context

docling-graph extraction is **schema-guided**: it requires a Pydantic template/typed ontology to
extract against (`ontology/clause_template.py::Clause`, `capabilities/dg_extraction.py::ContractParties`,
`skills/requirement_extraction/template.py`). That schema is the thing our query precision depends on:
typed property matching, function routing, and the compliance applicability matcher are all only precise
because extraction is normalized into a **bounded, typed vocabulary**. Today that ontology is hand-crafted
(`ontology/contract_bridge.ttl` + `compliance_bridge.ttl`, grounded in FOLIO / ODRL / LKIF / PROV-O), kept
consistent across **four synced artifacts** — `contract_bridge.ttl`, `clause_template.py::Clause`,
`contracts/property.py` (`PropertyDimension` / `CLOSED_VOCAB`), and `spans/symbolic_validation.py`
(`FUNCTION_APPLICABLE_DIMS`) — guarded by `tests/contracts/test_ontology.py`.

Two questions forced this ADR:

1. **Cold-start.** A brand-new customer in a domain we have no schema for still needs a schema for
   docling-graph to extract anything typed. Hand-crafting one from zero per domain does not scale, and it
   contradicts the generic-customer lens (ADR-0049): a new SME's ingest→KG→query must benefit without us
   authoring a bespoke `.ttl`.

2. **Can we build the schema/KG automatically from text (pure NLP/NLI: OpenIE/SVO triples, NER + relation
   extraction, LLM triple/ontology induction), and would evolving it incrementally as documents ingest
   improve precision?**

### What automatic methods actually deliver (the honest reliability landscape)

- **OpenIE / SVO via dependency parsing** (spaCy is in our stack; OpenIE is a listed core lib): per-triple
  precision ~50–70%, and relations are **surface strings** not typed predicates ("owns"/"is the owner
  of"/"shall own" become different edges). A firehose, not an ontology.
- **LLM schema-free triple extraction** (GraphRAG-style): higher per-triple quality (~80%+) but the
  **corpus-level consistency problem is unsolved** — same concept → many predicate strings, same entity →
  many nodes → structured queries fail.
- **LLM ontology induction** ("draft a schema from this corpus"): a good **draft TBox**, reliable enough to
  **seed** a schema, never to be the unattended schema.
- **Schema-guided extraction (ours)**: highest precision + consistency, because the schema **constrains**.

The core insight: **the hard part of automatic KG construction is not extracting triples (LLMs do that
well) — it is normalization** (entity resolution, relation/predicate canonicalization, type consistency).
A schema is exactly the artifact that solves normalization by construction. So "extract schema-free, then
induce the schema from the triples" is circular, and a schema-free stored graph would *lower* our query
precision, not raise it.

## Decision

Adopt a **bootstrap-then-evolve** strategy that keeps schema-guided extraction as the precision backbone and
uses automatic methods only where they are reliable — as a **draft** (seeding) and a **discovery signal**
(growth), always human-approved. Concretely, three layers:

1. **Seed (automatic, human-reviewed) — solves cold-start.** For a new domain/customer with no schema, run
   **LLM ontology induction** over a *sample* of their corpus to draft a starter ontology (entity types,
   relation types, property dimensions, deontic mapping). OpenIE/SVO/LLM triples are the *discovery input*
   to this draft (what entities/relations exist), never the stored graph. A human reviews the draft (per the
   generic-customer lens) and it becomes the schema docling-graph enforces. This is the ingestion-side
   analogue of COMP-VERDICT-GENERIC's always-answer — but done as *bootstrap-then-curate*, because ingestion
   produces the **stored** graph queries depend on, so it cannot tolerate the schema-free path the query side
   can.

2. **Grow (semi-automatic, human-curated) — improves precision over time.** Evolve the schema in small,
   curated increments using the **gap machinery we already have**: the `OTHER` / `other_label` extraction
   channel (`spans/clause_function_classifier.py`) and the gap analyses (`scripts/ontology_dimension_check.py`,
   `scripts/curate_taxonomy_gaps.py` — the FOLD/DROP/ADD loop from ONT-1/ONT-2). Each curated addition lets
   the extractor capture *more, precisely*. This is the ADR-0049 master lever, now standing as the growth
   path. **Precision improvement is expected here** — as a *replacement* for the schema (pure open-IE → KG)
   it would *lower* precision; as a *curated discovery-driven growth* of the schema it raises it monotonically.

3. **Feedback-driven evolution (the living ontology) — the product loop.** Two complementary user surfaces:
   - **Expert path:** the customer creates / uploads / edits their domain ontology directly (for users with
     ontology skill or an existing schema).
   - **SME feedback path:** the customer flags an imprecise match at query/verdict time ("you missed X",
     "got Y wrong", "this should be a Z") → a **feedback-processing agent** infers the schema gap and
     proposes a **constrained, add-only `.ttl` delta** (a new type / dimension / value / relation, with
     near-duplicate FOLDing) → the customer **approves the fixed outcome** (see trust mechanism) → the delta
     is threaded through the four synced artifacts under the `test_ontology.py` lint + full test gate → and a
     **bounded subset of previously-ingested documents is re-ingested** to populate the new schema fields.

### Load-bearing engineering facts this rests on

- **Re-ingest is already a first-class, safe operation.** The pipeline's `is_done(doc)` resume seam
  (`subgraphs/contract_ingestion_pipeline.py`) + idempotent upserts + the async/lossless envelope (ADR-0050)
  mean selective re-ingest = mark the affected docs not-done and re-run; it is non-destructive and bounded
  (only affected docs), streamed X/N, and dead-letters irrecoverable failures. A schema delta drives
  re-ingest by scoping *which* docs are marked not-done (those with `OTHER`-tagged content matching the new
  concept, and the docs the feedback came from).
- **Consistency is enforced, not hoped for.** Any ontology delta must keep `tests/contracts/test_ontology.py`
  and the full suite green before it lands — auto-generated deltas are CI-gated, never trusted blind.
- **Trust for a non-expert.** The approval unit is **the fixed outcome, not the `.ttl` syntax**: re-run the
  user's flagged case with the proposed schema and show "your case, before vs after." The SME approves
  because it now matches correctly.

## Consequences

Positive:
- New domains get a schema **automatically** (seed), removing the per-domain hand-crafting bottleneck — the
  cold-start answer the generic-customer lens needs.
- Precision **improves with use**, per tenant, human-approved, on infrastructure we already built (gap
  channel + resume/upsert + async/lossless).
- The ontology becomes a **living, usage-driven asset** rather than a static hand-crafted artifact, without
  paying the precision cost of a schema-free graph.

Negative / risks (and mitigations):
- **Auto-editing the four synced artifacts is the riskiest piece.** Mitigation: constrain feedback-driven
  changes to **add-only, from a template** (new dimension / type / value / relation); approve the **diff**;
  CI-gate on the ontology lint + tests.
- **Ontology drift / bloat** (the open-IE fragmentation problem, slower). Mitigation: the curation agent
  FOLDs near-duplicates (as `curate_taxonomy_gaps` does); a periodic consolidation review.
- **Per-tenant vs shared ontology** is an open product decision. Default direction: per-tenant domain
  ontologies + an optional curated shared base; additions do not silently cross tenants.
- **Re-ingest cost** is real but bounded (affected docs only, async).

Not decided here (deferred to the product SPEC):
- The concrete UI/agent surfaces (upload/edit editor; the flag-a-match affordance; the approval view).
- Whether the seed step ships before or after the first product cut.
- The per-tenant vs shared-base policy and any cross-tenant learning.

## Alternatives rejected

- **Pure schema-free open-IE/SVO KG as the primary graph.** Rejected: lowers query precision by removing the
  normalization our typed queries depend on. Open-IE/SVO stays a *discovery signal*, not the stored graph.
- **Fully-automatic unattended ontology induction (no human).** Rejected: reliable as a draft, not as the
  schema; contradicts the human-in-the-loop discipline (ADR-0049).
- **Hand-craft a bespoke `.ttl` per customer.** Rejected: does not scale; contradicts the generic-customer
  lens.
