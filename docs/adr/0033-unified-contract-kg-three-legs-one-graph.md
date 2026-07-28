# ADR-0033: One unified contract KG; the three legs are scoped queries over it

Status: Accepted (2026-07-28) — plan approved; build not yet started (resume point).

## Context

We have three retrieval "legs": A = intra-contract QnA (the demo leg — highlight/extract clauses in a *given*
contract), B = cross-corpus clause retrieval, C = cross-corpus relational/multi-hop over an entity graph.
Leg C's entity KG (`Party` + `CONTRACTS_WITH`/`AFFILIATE_OF`) is **built** via docling-graph + granite-4.1-8b
(GP-1B, real relational recall 0.991). We planned a separate clause KG for Leg B, and asked whether Leg A
could have its own intra-contract KG.

Two realizations: (1) intra-contract text is not different from cross-corpus text — the same extraction yields
parties, clauses, properties, and their relations; (2) therefore the per-contract KG is just the per-contract
**subgraph** of the same graph. Leg C's edges are per-contract co-party facts aggregated; Leg B is the clause
subgraphs unioned; Leg A is the graph filtered to one `contract_id`. Grounded research (FOLIO/ODRL/no standard
clause-ER ontology; KG helps precision/composition/citation on multi-constraint queries, not simple recall)
supports building it as a standards-bridged extension of the existing property graph, not a greenfield.

## Decision

Build **one unified contract KG** (`Contract`/`Party`/`Clause`/grounded value nodes + typed provenance-carrying
edges: `PARTY_TO`, `HAS_CLAUSE`, `CONTRACTS_WITH`, `AFFILIATE_OF`, `COVERS`, `CAPS`, ODRL
`GRANTS`/`PROHIBITS`/`REQUIRES`, `HAS_MUTUALITY`, `GOVERNED_BY`, `REFERENCES`) in the single ArcadeDB store.
Each leg is a **scoped query** over it: A = scoped to one `contract_id`; B = `Clause` nodes cross-corpus (as a
filter/rerank feature over the BGE base pool + pointwise-Gemma reranker, never a standalone retriever —
reachability ≠ rankability); C = `Party` traversal cross-corpus (done).

Ground in three layers: **FOLIO** (type IRIs, already aligned) + **ODRL** (W3C, the rights/duties relationship
spine) + a **small custom OWL bridge** that promotes the existing flat `PropertyDimension`s into typed edges
to closed-vocab value nodes and declares clause-types `subClassOf` their FOLIO IRIs (PROV-O for provenance).
Construct with the GP-1B recipe (the `kg-extraction-recipe` Skill): `docling-graph template from-ontology` ->
Pydantic template -> per-span extraction (granite-4.1-8b, the Leg-C model; no A/B, DeepSeek = below-par
contingency only -- KG-0 gate 2026-07-28; gated by the property-grounding judge ADR-0028) -> resolve/ground
(CIK + `PRIVATE:` sentinel; values -> FOLIO/ODRL IRIs) ->
write. Plan: `docs/unified_contract_kg_plan.md` (tasks KG-0..KG-6).

## Consequences

- **No third extractor.** The property graph (ADR-0025/0026, `Clause -> PropertyDimension` flat values) is
  *upgraded* to typed ER edges + grounded value nodes; Leg C is already the entity slice; Leg A gets the
  per-contract view for free. One graph, three scopes.
- **Leg A gains structured, relational, cited intra-contract QnA** (disambiguate same-type clauses by property;
  party↔clause role questions; aggregation; cross-clause refs) beyond clause-type highlighting.
- **Scope is bounded:** a small closed-vocab bridge OWL, not a full clause ontology or a deontic reasoning
  engine (that is compliance/orchestration scope, near the two-halves boundary).
- **The win is precision/composition/citation, not raw recall** — measured on multi-constraint/relational
  queries (grade≥2 floor), not simple lookups. Clause-internal extraction is harder (semantic) than party
  extraction; the grounding judge is the gate and the model is fixed to granite-4.1-8b (no A/B).
- **Ask-first data-model change** (new nodes/edges + ODRL/bridge ontology) — KG-0 goes through a schema-review
  gate. New dependency (ODRL/rdflib for the bridge) is ask-first.
- **KG-0 gate resolved (2026-07-28):** approved the bridge schema — distinct `HAS_*` edges; `HAS_*`/`EXCEPTS`/
  `BOUNDED_BY` naming; **build typed, retire the flat `HasProperty` graph** (not a fallback); ODRL adopted at
  **full depth** (deontic core + `odrl:constraint` for cap/temporal bounds); the `.ttl` authored+validated at
  KG-1; **KG-2 extracts with granite-4.1-8b, no A/B** (DeepSeek = below-par contingency only). Design:
  `docs/unified_contract_kg_ontology_bridge.md`.
