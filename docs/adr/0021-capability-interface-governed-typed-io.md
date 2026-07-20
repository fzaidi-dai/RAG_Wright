# ADR-0021: Emit a governed typed I/O interface (`capabilityInterface`) on authored ARD manifests

Date: 2026-07-20. Status: Accepted. The RAG-side record of the cross-repo coordination with GraphWright
ADR-0030 (their governed capability interface). Mirrors, on the capability half, the vendor-extension field
their lowering checker verifies a realization against. See the handoff exchange in
`docs/handoff/2026-07-20*_graphwright_*.md`.

## Context

GraphWright's lowering pass realizes a plan against our discovered capabilities and runs a deterministic
checker: do the bound capabilities' inputs/outputs actually chain to produce what a step needs? Our manifests
carried a rich `description` and representative queries but **no typed I/O interface**, so their checker could
only verify a *model's asserted* interface — a wrong interface still "verified" (they caught a capability
force-fit to a step it cannot do). To make "verified" mean verified against governed truth, each capability's
real I/O must be governed data we own and declare, not inferred per call.

We confirmed the interfaces against the **real bound callables** (not the manifest prose). The load-bearing
correction: `reranking` consumes passage TEXT, not chunk ids — the exact force-fit their checker exists to
catch, surfaced before anything shipped.

## Decision

**Author and emit an optional `capabilityInterface` block on our ARD manifests**, using GraphWright's nominal
type model. It is a GraphWright **vendor extension**, not part of the ARD envelope; ARD-standard consumers
ignore it.

1. **Nominal typing, not compound records.** The checker compares a port's type as an **opaque NAME string**
   (two ports chain iff their type names are equal); it does no field-level reasoning. A distinct type name is
   all that is needed to force the right chain (e.g. `reranking` in = `chunk_with_text`, produced only by
   `chunk_read`, so the checker forces a rehydrate between an id-only producer and reranking). We rejected
   proposing compound/record types — GraphWright reuses one `TypedInterface` contract across its stack and
   will not fork it. Our finding that `{text, chunk_id}` was too coarse is fixed inside the nominal model by a
   **richer set of agreed scalar names**, not by adding structure.
2. **The vocabulary is a mirrored shared contract.** `NOMINAL_TYPE_VOCABULARY` in `capabilities/ard.py`
   mirrors GraphWright ADR-0030 section 3 verbatim — the six names `text`, `chunk_id`, `chunk_with_text`,
   `scored_chunk`, `graph_answer`, `cited_extract` (see the fusion resolution below; `fused_chunk` was
   retired). Every declared type name is validated against it at author
   time, so a typo or stray list sugar (`chunk_id[]`) fails in our own suite instead of silently breaking a
   chain check on GraphWright's side — the same deliberate-duplication + conformance-test discipline as the
   RegistryEntry schema mirror (ADR-0003/0005) and the canonical-slug set. Changing the vocabulary is a
   cross-repo coordination point.
3. **Data I/O only, not config.** The interface carries only the data that flows between orchestration steps
   as named channels (the query, chunk references, the answer). The callable's tuning/deps (model, keys,
   thresholds, top-k, store/embedder seams) are deployment config and stay out. Cardinality (list vs scalar)
   is not encoded — a port carrying many candidates and one carrying a single value both use the element name.
4. **Schema lockstep (`extra="forbid"`).** Both our `RegistryEntry` mirror and GraphWright's `entry.py` forbid
   unknown fields, so `capabilityInterface` is a **coordinated** schema addition, not a free additive drop:
   neither side ships a manifest carrying it until both schemas declare it. Both now do. The top-level field is
   camelCase (`capabilityInterface`, via `to_camel` on `_ArdModel`); the nested `CapabilityInterface` is a
   plain `BaseModel` with no ARD alias, so its keys stay snake_case (`success_criterion`) — matching
   GraphWright's `TypedInterface`, whose `extra="forbid"` loader rejects camelCased inner keys.
5. **Scope: the query→answer graph.** Declared on the 7 capabilities GraphWright's checker verifies —
   `hybrid_search`, `chunk_read`, `reranking`, `graph_query`, `fusion`, `rlm_synthesis`, `generation`. Other
   capabilities declared no interface at first. **Extended in T44 (see addendum)** to the ingestion→graph caps;
   `rlm_method` remains ungoverned by design.

`generation`'s abstain outcome is a boolean `abstained` flag on the answer record (empty `citations`), not a
distinct typed channel and nothing downstream gates on it, so it stays in the payload and is named in
`success_criterion`, not given a port.

## Consequences

- The checker verifies a realization against our real, governed interfaces, not a model's guess. The
  `reranking`-needs-text fact becomes structural: only `chunk_read` produces `chunk_with_text`, so the lowering
  must rehydrate before reranking.
- Two chain-level placements were surfaced to GraphWright (handoff 2026-07-20b): a rehydrate must precede
  reranking; and `fusion`'s output vs `chunk_read`'s `chunk_id` input. **Resolved (GraphWright, 2026-07-20):**
  `fusion` output retypes to `chunk_id` — it *is* an id-only reference, and by the section-3 provenance rule the
  `sources[]` sub-field does not fork the type name (no consumer gates on it). This unblocks the valid
  `fusion -> chunk_read -> synthesis` tail and retires `fused_chunk`, dropping the vocabulary to six names. The
  rehydrate-before-reranking placement stands as the intended structural consequence.
- A drift between a capability's real I/O and its governed manifest interface now fails a RAG test
  (`test_governed_capabilities_declare_the_confirmed_interface`), not silently at GraphWright's bind.
- The vocabulary and the field shape are cross-repo coordination points; a change on either requires a
  coordinated update to both `ard.py` and GraphWright's `entry.py`.

## Addendum (T44, 2026-07-20): extend governance to the ingestion→graph capabilities

GraphWright found the residual force-fit hole: the "no false resolves" guarantee held only for governed caps,
and their model force-fit an *ungoverned* cap to a step no cap does (1 run in 5). The fix is to govern the rest
of the bound catalog, same additive pattern.

- **Governed 7 more** (all bound in the ingestion/graph pipeline): `parsing`, `rlm_chunking`, `embedding`,
  `graph_extraction`, `entity_disambiguation`, `entity_resolution`, `vision_to_text`. Grounded against the real
  callables. The ingestion→graph chain type-checks end to end:
  `document → parsed_doc → chunk → {embedding, extraction} → entity_cluster → resolved_entity`, and
  `image → text`.
- **`rlm_method` stays ungoverned by design.** It is a shared method skill `require`d by `rlm_chunking` and
  `rlm_synthesis`, never bound as a data-processing node — no pipeline data I/O, so a governed interface would
  be a type with no producer or consumer. (Answers GraphWright's "bound vs internal-only" ask.)
- **Vocabulary extended 6 → 14** with the ingestion data shapes: `document`, `parsed_doc`, `chunk`, `embedding`,
  `extraction`, `entity_cluster`, `resolved_entity`, `image`. Grounded shape decisions: the ingestion `chunk`
  is kept **distinct** from the retrieval `chunk_with_text` (it carries the summary `embedding` needs;
  different producers/consumers — collapsing them would let a wrong chain type-check); `parsing` outputs a
  `parsed_doc` *handle* (not flat text); `embedding` consumes `chunk` (not `text`); `entity_resolution` is a
  distinct-typed **two-input** fan-in (`entity_cluster` + `extraction`), like `fusion`.
- **`vision_to_text` is a standalone `image → text` leaf** whose output is not `parsed_doc`, so it does not
  chain into `rlm_chunking` directly. Typed truthfully so GraphWright's checker catches the gap; placing it
  (route image-only sources through parsing's OCR, or a `text → parsed_doc` adapter) is their graph-design call.
- **Vocabulary lockstep honored:** GraphWright mirrored the 8 new names in their `NOMINAL_TYPE_VOCABULARY`
  (`RETRIEVAL_TYPES` ∪ `INGESTION_TYPES` = 14) and confirmed the 7 interfaces before we re-emitted. All 14
  governed manifests are now emitted into `~/.air/registry`; their `RegistryStore` round-trips all 14. This
  closes the last force-fit hole — "no false resolves" holds across the whole bound catalog.
