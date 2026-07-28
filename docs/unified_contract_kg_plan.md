# Plan: Unified Contract KG (one graph, three legs as scoped queries) — 2026-07-28

## The core reframe

The three retrieval "legs" are **three query patterns over one knowledge graph**, at different scopes — not
three separate systems. Build **one unified contract KG** and each leg is a scope/traversal over it, fused
with dense retrieval where relevant:

| Leg | Query over the KG, scoped to… | Status |
|---|---|---|
| **A** — intra-contract QnA (the demo leg) | one `contract_id`: its parties, clauses, properties, intra-contract relations | classification-based today; **KG-upgraded here** |
| **B** — clause retrieval (cross-corpus) | all `Clause` nodes by type + typed properties (as a filter/rerank feature) | property graph today; **upgraded to typed ER here** |
| **C** — relational (cross-corpus) | all `Party` nodes (`CONTRACTS_WITH`/`AFFILIATE_OF` traversal) | **DONE** (GP-1B, real recall 0.991) |

Leg C's `CONTRACTS_WITH` edges are per-contract co-party facts aggregated; Leg B is the per-contract clause
subgraphs unioned; Leg A is the same KG filtered to one contract. So there is **no third extractor** — build
one contract KG; the legs are scopes over it.

## Target schema (one KG in ArcadeDB, same store)

- **Nodes:** `Contract`, `Party` (→ EDGAR CIK / `PRIVATE:<key>`), `Clause` (typed to a FOLIO IRI), and
  grounded property/value nodes (`ClaimType`, `Right`, `Scope`, `Jurisdiction`, `Amount`, …).
- **Edges (typed, provenance-carrying):** `PARTY_TO` (with role), `HAS_CLAUSE` (clause_type), `CONTRACTS_WITH`,
  `AFFILIATE_OF` (done); and the new clause-internal layer: `COVERS`, `CAPS`, `GRANTS`/`PROHIBITS`/`REQUIRES`
  (ODRL Permission/Prohibition/Duty with `action`/`target`/`assigner`/`assignee`/`constraint`), `HAS_MUTUALITY`,
  `GOVERNED_BY`, `REFERENCES` (cross-clause). Every node/edge cites its span/chunk (FR-Q.6, PROV-O
  `EXTRACTED/INFERRED/AMBIGUOUS`).

## Grounding stack — three layers, not a monolith (per ADR-0033 + research)

1. **FOLIO** (`openlegalstandard.org`, CC-BY) — node/value **type** vocabulary (clause/asset/party-role IRIs).
   We already align `ClauseCategory → FOLIO IRI` in `contracts/property.py`; keep it.
2. **ODRL** (W3C Recommendation, downloadable OWL) — the **relationship spine for rights/duties** clauses
   (exclusivity, license, distribution grants → `Permission(action, target, assignee, assigner, constraint)`).
3. **A small custom OWL bridge** (~dozens of classes/properties) — promotes our existing flat
   `PropertyDimension`s (mutuality, claim_scope, covered_subject, cap_basis, …) into **typed object properties
   to closed-vocab value nodes**, declares clause-types `subClassOf`/`equivalentClass` their FOLIO IRIs, and
   uses PROV-O for provenance. This is the only ontology we author; keep it small + closed-vocab.

## Construction — reuse the GP-1B recipe (the `kg-extraction-recipe` Skill)

1. **Compile** the bridge OWL → a Pydantic clause template via docling-graph `template from-ontology`
   (deterministic, no LLM; accepts OWL/RDFS/SKOS/LinkML).
2. **Extract** per-clause typed triples from the **operative spans** (the right granularity — a span is a
   clause) via docling-graph. **Model = benchmark, not assumed**: re-A/B **granite-4.1-8b vs DeepSeek** for the
   *harder, more semantic* clause-property/role extraction (entity extraction chose granite; clause structure
   may differ), gated by the **property-grounding judge (ADR-0028)**.
3. **Resolve + ground:** parties → EDGAR CIK (+ `PRIVATE:<key>` sentinel, the GP-1B lever); clause values/
   predicates → FOLIO/ODRL IRIs. Reuse `resolve_extracted` + the sentinel-alignment pattern.
4. **Write** the unified KG to ArcadeDB (Contract/Party/Clause/value nodes + typed edges), content-hash gated.

## Retrieval integration (layer, don't replace) — reachability ≠ rankability

- **Leg A:** serve intra-contract QnA by querying the KG scoped to one `contract_id` — answering questions
  classification can't: disambiguate multiple same-type clauses by properties, party↔clause **role** questions
  ("who is the indemnifying party?"), aggregation ("all of X's obligations"), cross-clause refs — all cited.
  This upgrades the demo backend (`serve_highlight`) from clause-type filter to structured QnA.
- **Leg B:** extend the query-decomposition (one LLM call → function + property enums + hypothetical clause) to
  emit **typed edge constraints**; use the ER subgraph match as a **hard filter / boost + rerank feature over
  the BGE base pool**, feeding the **pointwise-Gemma reranker** (our operating point) — NOT a standalone
  retriever. Slots into `eval/function_property_rerank.py`.
- **Leg C:** already wired (`eval/relational_eval.py`).

## What is done vs new

- **Done (GP-1B):** the entity slice (`Party` + `CONTRACTS_WITH`), the docling-graph+granite pipeline,
  resolution + PRIVATE sentinel, the eval discipline, the reusable Skill, the Granite-on-Modal server.
- **Seed to upgrade:** the property graph (`Clause → PropertyDimension` flat values, FOLIO-aligned).
- **New:** ODRL grounding + the bridge OWL; per-contract clause+property+role+cross-clause extraction; the
  unified schema; the scoped-query serving for Leg A and the typed-constraint rerank feature for Leg B.

## Tasks (contract-first TDD, one task + gate each — the standing loop)

- **KG-0** — Ontology bridge design (FOLIO alignment audit + ODRL adoption + the small custom OWL bridge:
  clause-type classes, property dimensions → typed edges, value nodes, PROV-O). **Schema-review gate** (this is
  an ask-first data-model change).
- **KG-1** — Compile the bridge OWL → Pydantic clause template (`template from-ontology`); lint hermetically.
- **KG-2** — Per-clause typed extraction from spans; **model A/B (granite vs DeepSeek)**; grounding-judge gate;
  hermetic tests + a live smoke.
- **KG-3** — Resolution/grounding (parties→CIK/PRIVATE; values/predicates→FOLIO/ODRL IRIs) → write the unified
  KG to ArcadeDB; store schema + write path.
- **KG-4** — **Leg A**: intra-contract scoped-query serving (structured, relational, cited QnA); enhance the
  demo backend.
- **KG-5** — **Leg B**: typed-edge constraint match as a filter/rerank feature over the BGE base pool +
  pointwise-Gemma reranker (into `eval/function_property_rerank.py`).
- **KG-6** — Eval: extraction recall vs a gold clause-KG; A/B on the **harder** queries (intra-contract
  multi-constraint/role/aggregation; cross-corpus conjunctive) with the grade≥2 floor — expecting lift on the
  conjunctive/relational queries, not the simple lookups.

## Scope discipline + honest gates

- Keep the bridge OWL **small and closed-vocab**; do NOT build a full open-domain clause ontology or a
  LegalRuleML deontic reasoning engine (that's compliance/orchestration scope, near the two-halves boundary).
- The KG is a **precision + composition + citation** instrument: it wins on multi-constraint/relational/
  aggregation queries; simple single-clause lookups already work — measure the marginal gain, don't assume it.
- Clause-internal extraction is harder than party extraction (semantic) — the property-grounding judge is the
  quality gate; the model is a benchmark.

## Reference / reuse
`src/rag_wright/capabilities/dg_extraction.py` (the recipe), `scripts/populate_entity_graph_extracted.py`
(driver pattern + cache), `eval/relational_eval.py` (leg C), `src/rag_wright/contracts/property.py` (the
property-dimension seed + FOLIO alignment), `eval/function_property_rerank.py` (leg-B rerank slot),
`capabilities/highlight_serve.py` (leg-A serve to upgrade). Skill: `kg-extraction-recipe`.
