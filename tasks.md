# tasks.md: RAG_Wright task ledger

Phase 2 output. The persistent, cross-session task ledger and shared memory of progress. Derived
from `plan.md` (Phase 1) and `SPEC.md` v0.1, honoring ADR-0001 (stack) and ADR-0002 (corpus).

This is the **capability half** only. Each task builds and registers one FR-C / FR-I / FR-Q
capability, or a foundation seam, as ordinary tested software. Registration is twofold and is part
of every capability's definition of done: internal registration (T6, which the Model Context
Protocol surface exposes) and Agentic Resource Discovery (ARD) registration (the manifest the
GraphWright compiler discovers and binds against); see "ARD registration" below. The ingestion and
query **graphs** are compiled separately from the Orchestration Spec by the GraphWright compiler
and are not built here. Any task that looks like "wire the pipeline into a graph" is compiler work,
not this repo's.

> Conventions: acronyms expanded on first use, no em dashes, plain phrasing.

> **HYGIENE (recurring, tracked 2026-07-17):** external-agent scratch has twice appeared in `src/`
> (turn 1: a broken `skills/rlm/__init__.py` + an external `_MAX_DEPTH` cap; turn 2: a dead
> `handle_leaf_depth_3` Python mirror of the JS depth-cap branch + its export + ~13 untracked test
> runners). The invariant at risk is **tested-equals-shipped** — a second copy of real logic (a Python
> mirror of `RLM_WORKFLOW_JS`) is not guarded by the drift test and can be mistaken for, or drift from,
> the truth. Standing rule: **any external edit to the RLM files (`agent.py`, `SKILL.md`,
> `RLM_WORKFLOW_JS`, `skills/rlm/__init__.py`) is suspect until verified** — those are where a silent
> mirror or broken import does real damage. Verify (drift test + suite + no unguarded logic copy) before
> trusting an external RLM edit.

> **LATENT HARDENING ITEM — RLM worker query/slice threading (GraphWright grounding 2026-07-18; CONFIRMED
> accurate; DRY-RUN VERDICT: did NOT bite → parked, not urgent).** The RLM leaf dispatch is **query-less AND
> slice-less**:
> `task({description: "handle leaf depth " + depth, subagentType: "rlm_slice_worker"})`
> (`agent.py:129/137/154`) passes no query and no slice items. The worker system prompt
> `_EXTRACT_WORKER_PROMPT` (`rlm_synthesis.py:59`) says *"extract the facts in this slice that help answer
> the question"* but the workflow provides NEITHER the slice text NOR the question; the query lives only in
> the orchestrator's `_request` (*"Question: {query}"*, `:231`). So whether a worker extracts
> query-relevantly depends on the **orchestrator model choosing** to thread the query (and slice) into the
> dispatch — model-driven, not enforced. Same silent-gap class as the working-set findings (SKILL.md
> under-specifies; a capable model papers over it until it doesn't). **Affects standalone synthesis too, not
> just the joint run.** My tests do NOT catch it — the sub-agent responders are stubs that return canned
> output regardless of dispatch content. **HELD, do NOT fix now:** GraphWright's dry-run is instrumented to
> check whether extractions are query-relevant (not just whether citations resolve). Fixing now would treat
> an unconfirmed symptom AND destroy the dry-run's evidence of the current, unmodified behavior. Sequence:
> dry-run verdict → if it BITES, thread the query (+ slice) into the worker dispatch (a real fix that also
> improves standalone synthesis, and add a non-stub test that asserts the worker receives the query); if it
> does NOT, record as a latent hardening item (enforce-what-works-by-luck).
> **VERDICT (GraphWright dry-run, 2026-07-18): did NOT bite.** On real "Audit Rights" data the extractions
> were query-relevant (worker got the query) AND extract-matched-chunk (worker got the slice) — the
> orchestrator model threaded both. So it works, but by model choice, not enforcement → **parked as a latent
> hardening item, not urgent (integration-first).** When picked up: thread query+slice explicitly into the
> dispatch so it's enforced, and add a non-stub test asserting the worker receives them.

---

## Last approved / next up

- **SPECCED (not yet built), 2026-07-22: FR-K — embedding-free OKF navigation (T45-T53 + GATE-3a/GATE-3).**
  New experimental capability block added to `SPEC.md` (FR-K.1-K.9) and this ledger, motivated by the T41
  query-representation-gap diagnosis: compile the chunk corpus into an Open Knowledge Format (OKF) bundle
  and retrieve by programmatic traversal of signposts (indexes, frontmatter, links) instead of vector
  similarity. Corpus-neutral capability; ACORD is the first validation corpus. Categories manufactured via
  `graph_extraction`; index descriptions reuse T-SUM summaries; bundle is a gitignored data artifact.
  **First execution slice = T45 gold labels → T46 chunk-only compile → T47 reachability analyzer → T48
  category-label control → GATE-3a** (per-corpus reachability kill-switch, no traversal model call spent).
  Behind GATE-3a: T49 cross-linking, T50 `okf_navigate` traversal, T51 trace-and-iterate → GATE-3 (graduate
  or remove) → T52 lifecycle, T53 strategy memory. Harness-profile seam from the feeder draft was dropped
  (traversal binds the T11 model-profile seam). OKF repo cloned + graphify-indexed for grounding at
  `/Users/farhan/work/knowledge-catalog/okf/src/graphify-out/graph.json`. ADR-0022 records the decision.
  Pre-T45 diagnostic (ADR-0022 addendum): category signpost **decoupled** from graph_extraction (41-CUAD
  covers only 67% of ACORD gold; a direct corpus-appropriate classifier hits 91.6%).
- **LAST APPROVED (committed): T45** — ACORD gold-chunk labels. 57 test queries → 475 distinct gold chunks
  (grade≥2), exact qrel-corpus-id→chunk_id map (0 unmapped, validated against the T40 sidecar), any/all-gold
  readings, deterministic stratified debug split (7 debug / 50 held-out headline), + 475 qrels-induced silver
  category labels (`metadata.category`) for T46/T48. 7 tests, full suite 441+27. Artifact
  `data/eval/okf_gold.json` (gitignored, rebuildable).
- **LAST APPROVED (committed): T46** — OKF bundle compile (chunk-only). `okf/{document,enrich,compile,lint}.py`:
  gated+concurrent enrichment (category + one-line description per clause) via the new cheap `OKF_ENRICHMENT`
  model role (Gemma-4-26b-a4b, ADR-0023 — 100% category agreement with DeepSeek on the bench, ~4x cheaper),
  deterministic compile (tree root→category→clause, byte-faithful bodies from the T40 sidecar, index.md per
  level, content-hash gate, selective recompile), conformance linter. Real run: **3,931 clauses, 3,491
  categorized** into ACORD's 9 (440 `_uncategorized`, 1 fallback); lint clean. Category signpost **decoupled
  from graph_extraction** (ADR-0022). Registered `okf_compile` (category-3, no manifest). 10 tests, full suite
  451+27. Bundle gitignored at `data/acord/okf/bundle`.
- **LAST APPROVED (committed): T47** — reachability analyzer + ablation (`eval/reachability.py`, `eval/ablation.py`).
  Model-free ceiling over the ACORD gold: **any-gold 0.930, per-gold recall ceiling 0.776, all-gold 0.491**,
  connectivity 1.000; ablation shows **descriptions carry the ceiling** (cost 0.351), category tree −0.018, tags/
  frontier ~0. **DECISION (ADR-0022 addendum 2): 0.776 > bar 0.667 > baseline 0.379 → VIABILITY SETTLED, not
  go/no-go. T50 is mandated to REALIZE this proven ceiling by refinement+iteration; a shortfall = policy/agent bug
  to fix via T51, not a verdict against FR-K.** GATE-3a resolved = PROCEED; GATE-3 reframed to realization-vs-ceiling
  + cost-vs-control (no "remove-for-viability"). 9 tests, full suite 460+27.
- **LAST APPROVED (committed): T48** — category-label control arm (`eval/category_retrieval.py`). **recall@50
  0.134, containment 0.895** over the 57 test queries, 0 model calls. Category is a strong bucketer (gold in the
  right category 89.5%) but a useless localizer (no intra-category ranking → 0.134, below baseline 0.379). The
  containment→recall gap IS the within-bucket localization OKF descriptions provide → **the cheap control does
  not capture the lift; T50's description-localization is justified.** 4 tests, full suite 464+27.
- **LAST APPROVED (committed): T49** — cross-linking (`src/rag_wright/okf/links.py`), embedding-free
  (Option-1: shared distinctive-term edges, bounded degree ≤8, df>100 skipped). 20,033 edges, mean degree 5.10,
  broken-link 0.0000. **Links RAISE the reachability ceiling: per-gold recall 0.776→0.845, all-gold 0.491→0.614**
  → Option-2 embedding-kNN NOT needed (unused fallback). Linter extended to scan concept-body links; reachability
  gained a `cross_links` channel. 6 tests, full suite 470+27. **NEXT UP: T50** — the `okf_navigate` traversal
  capability that REALIZES the 0.845 ceiling (interpreter + PTC + dynamic sub-agents; RLM machinery T15/T36/T37).
- **LAST APPROVED (committed): T44** — govern the remaining capabilities. `capabilityInterface` on the 7
  ingestion→graph caps (parsing, rlm_chunking, embedding, graph_extraction, entity_disambiguation,
  entity_resolution, vision_to_text), grounded in the real callables; `rlm_method` stays ungoverned (required
  skill, not a data node). Vocabulary extended 6→14 (ingestion shapes). **GraphWright mirrored the 14 +
  confirmed all 7**; all 14 governed manifests re-emitted to `~/.air/registry` — the last force-fit hole is
  closed ("no false resolves" across the whole bound catalog). Full suite **434 passed + 27 skipped**.
  **NEXT UP: nothing running — user picks** (parked candidates unchanged: T41 composition experiments / LLM
  reranker first, T30/T31/T32/T34/T35/T39/T18).
- **LAST APPROVED (committed): T43** — emit `capabilityInterface` typed I/O on our authored ARD manifests
  (GraphWright ADR-0030 / our ADR-0021). Nominal typing on 7 retrieval→answer manifests; fusion out retyped
  `fused_chunk`→`chunk_id` (GraphWright's call). Full suite 426 passed + 27 skipped.
- **LAST APPROVED (committed): T42** — RLM/synthesis latent-hardening (worker query+slice threading enforced,
  non-stub multi-worker split test, messy fixtures). Full suite 412 passed + 27 skipped.
- **LAST APPROVED (committed): T28 (rebuild)** — RLM synthesis = **recursive descent + kept `_reduce` ascent**.
  A `SliceExtractor` seam (`SeamSliceExtractor` via `build_rlm_agent`) decomposes the candidate set (fresh
  `rlm_decomposer` per over-large group, `rlm_slice_worker` extracts per leaf); the Python `_reduce` fan-in
  (kept, ADR-0016) combines. **Recursion GATED** here (ADR-0019): the descent recurses past depth one
  (opaque candidate set, decomposer fires at >1 depth, code-driven fan-out). Per-slice tool use proven;
  every extract cited. Live extract+synthesize **passed** (real deepseek: $500M + Delaware + citations). 6
  passed + 1 skipped; full suite **379 passed + 26 skipped**. **The T15→T17→T28 rebuild is complete** —
  immediate follow-up (needs approval): populate `grantedSubagents` in the 3 manifests + re-emit + notify
  GraphWright.
- **LAST APPROVED (committed): T17 (rebuild)** — RLM chunking = **LLM semantic boundary discovery** (mandatory,
  no fixed-size). A `BoundaryDiscoverer` seam (`SeamBoundaryDiscoverer` via `build_rlm_agent`,
  deepseek-v4-pro) returns spans over `document.texts` items; `_finalize_chunks` is the deterministic-
  given-boundaries layer (join, cap, **T-CHK floor/merge/near-empty preserved over the discoverer's
  spans**, id, validate); summaries stay the concurrent flat map. Recursion **not gated** for chunking
  (ADR-0019). Live boundary-quality test (a coherent clause kept whole) **4/4** on deepseek-v4-pro,
  including a clean semantic partition. **Caveat:** boundary quality is guarded ONLY by the opt-in live
  test — CI does not cover it; run `-m model` before trusting a chunking-boundary change. 14 passed + 2
  skipped hermetic; full suite **379 passed + 26 skipped**. Logged **T34** (no document upsert path
  exists). **Next after approval:** T28 (RLM synthesis).
- **LAST APPROVED (committed): T15 (B′ rebuild)** — RLM skill + reusable machinery (recursive dynamic
  sub-agents). Grounding overturned ADR-0015 Q2's self-dispatch: a self-referential sub-agent is **not
  constructible** on `deepagents==0.6.12` (eager roster compile in `SubAgentMiddleware.__init__`) — I
  surfaced it and you approved **design B′** (the interpreter re-dispatches a fresh `rlm_decomposer` per
  level; `grantedSubagents` unchanged, no manifest churn). Delivered: rewritten `SKILL.md`, `agent.py`
  (`build_rlm_agent` + the two sub-agent configs + shipped `RLM_WORKFLOW_JS`), ADR-0015 Q2 correction,
  and the 4 ADR-0016 fail-if-absent tests (written first, confirmed red vs pre-rebuild, now green;
  recursion asserts `maxSplitDepth>=1` + >1 decomposer level + teeth tests for flat-workflow and
  sequential-dispatch). Plus an **opt-in real-model smoke test** that caught a real defect — the method
  wired as a lazy `skills=` source was never read, so a real model flatten-and-hardcoded; fix (ADR-0018)
  = method into the orchestrator **system prompt** + firmer SKILL.md; redesigned around an opaque working
  set so full leaf coverage proves derivation; **10/10** live after the fix. 6 passed + 1 skipped; full
  suite **379 passed + 26 skipped**. Deterministic chunk core and `_reduce` untouched. **Next after
  approval:** T17, then T28; then populate `grantedSubagents` + re-emit.
- **LAST APPROVED (committed): T-DISP** — `requires_dynamic_dispatch` typed flag on `skill_runtime`,
  the ARD-contract groundwork for the recursive-RLM rebuild (ADR-0015/0017). Landed: the flag +
  implies-interpreter validator on `ard.py`, populated `true` on the 3 RLM manifests, conformance tests,
  ADR-0017. Self-dispatch **confirmed** on `deepagents==0.6.12` (name-based roster; self-referential
  `rlm_decomposer` constructs without infinite recursion) — last pre-T15 grounding item cleared. Full
  suite **373 passed + 26 skipped**. **After approval + commit: T15 stays HELD** until GraphWright's
  applier (translating the flag into the interpreter trigger) lands; then the T15→T17→T28 rebuild runs,
  each through its own gate, re-emitting with `grantedSubagents=["rlm_decomposer","rlm_slice_worker"]`.
- **Done this session (all committed):** T22 (reranking, RAC-22 **b2 still OPEN** pending ACORD), T-CHK,
  T-SUM, **T23/T23b/T24/T25/T26/T27** (graph extraction → fusion), **T28** (RLM synthesis, pre-rebuild),
  **T29** (answer generator + vision-to-text). ARD split of FR-C.9 → `generation` + `vision_to_text`
  (approved spec change). **GATE-2 runs 1+2 not adjudicated** (ADR-0011: CUAD not a retrieval benchmark);
  store bar kept-on-ArcadeDB, not marked met. **Still open (both ask-first): T33** (ACORD → closes
  GATE-2a + RAC-22 b2) and the post-graph **GATE-2b** RLM keep-vs-optional call. ArcadeDB container
  stopped (idle until T25/T33 re-run).
- **Prior last-approved:** **T21** (FR-C.3/FR-Q.1, RAC-21) — Hybrid search. Query-side capability: embed the
  query once (dense + sparse over the query text, via the T19 `Embedder` seam), hand both vectors to
  the new **semantic** query-side `Store.hybrid_search(dense, sparse, *, k, filters)` seam method, which
  the ArcadeDB impl fuses server-side by RRF (`vector.fuse`, the T14-proven SQL) into one ranked
  `Candidate` list honoring metadata filters. Each leg fetched to a `DEFAULT_CANDIDATE_POOL=100` pool,
  filter applied post-fusion, cut to `k`. Registered under FR-C.3 (`function`) + ARD manifest authored
  and published. Live `-m store` verified real server-side RRF (c1 dense-leg + c2 sparse-leg winners
  fuse to the top, weak c3 excluded) and a `source_doc_id` filter. **recall@k on the golden set is
  measurable through this capability but the golden-set run is deferred to GATE-2/T22.** Full suite 285
  passed + 15 skipped. **GATE-1 (2026-07-09): kept the RLM chunker, T18 deferred → earns-its-cost call
  is at GATE-2/T22; if unproven, make the chunker optional, not dropped.**
- **Prior:** **T19** (RAC-19) Embedding (BGE-M3); **T17** (RAC-17) RLM chunking; **T16** Parsing.
  Grounded the hybrid SQL against the official ArcadeDB docs + live 26.7.1, then tested end to end
  through the arcadedb-python `query()` method: `SELECT expand(vector.fuse(vector.neighbors(...),
  vector.sparseNeighbors(...), {fusion:'RRF'}))` returns a sensible fused ranking (dense-leg + sparse-leg
  winners fuse above the weak record), and a metadata filter composes via `... FROM (<fuse>) WHERE
  source_doc_id=...`. **GATE-2 signal: works as documented, no lean toward LanceDB** (early A-T1 signal;
  the real GATE-2 decision is on the golden set at T21/T22). ADR-0007 A-T1 section. `-m store` 2 passed;
  default suite 244 passed + 7 skipped. Server pinned to stable 26.7.1 (grounding correction `179ba3f`).
- **Prior:** **T13** (RAC-13) store seam + ArcadeDB schema; **T12** (RAC-12) forced-structured seam test.
  Live probes through the seam confirmed the empirical profiles (ADR-0006): **DeepSeek V4 Pro**
  (`deepseek/deepseek-v4-pro`) honors the forced tool call while reasoning (bare `function_calling`,
  no `extra_body`); **Qwen 3.7 Plus** (`qwen/qwen3.7-plus`) reproduces risk 3 (`<400> ... tool_choice
  ... in thinking mode`) and is fixed by the structured-only `extra_body={"reasoning":{"enabled":
  false}}`. Slugs corrected from T11 placeholders (also general default `google/gemma-4-31b-it`).
  Opt-in live test `tests/foundation/test_model_seam_structured.py -m model` (2 passed); default suite
  242 passed + 2 skipped, hermetic. `model`/`store` markers registered; `conftest.py` gates opt-in
  tests and loads `.env`.
- **Prior:** **T11** (RAC-11) model-profile seam; **T10** (RAC-10) EDGAR RELATIONAL golden set.
  **19 questions (16 one-hop + 3 two-hop)** built from the human-verified set into
  `eval/golden/relational/set.json` (**committed** as a verified fixture — the derived answer keys,
  the only durable copy in git; the gitignored triage file `data/edgar/verification_set.json` stays
  out; ADR-0005). Registered as `Archetype.RELATIONAL` in the harness; honest property recorded:
  `public-filer-centric, multi-hop-modest`. Rebuild anytime: `uv run python -m eval.multihop` (the
  `resolution` fields, not the rebuild, are ground truth). Along the way T10 also built the T23b
  canonicalizer (`corpus/canonicalize.py`) and the EDGAR loose/lookup helpers (all committed;
  ADR-0004). (T9 golden harness + CUAD set, 2,032 questions, remains the prior CUAD baseline.)
- **Phase 2 (Tasks) ledger** was approved with revisions (RAC prefix; FR-I.6 line redrawn; EDGAR
  multi-hop split out as T10; DeepSeek V4 Pro first in the model-profile seam; ARD registration
  added as a cross-cutting definition of done, T6 emits the manifest skeleton and each bound
  capability authors and validates it), committed `ae593c3`.
- **Open question carried in:** none blocking Phase 3. Matching strategy for entity resolution
  (§16.3) and ArcadeDB index specifics (§16.4) resolve at their own tasks (T8 / T13).

---

## How to read this ledger

- **Status values:** `todo`, `in-progress`, `awaiting-approval`, `done`. A status becomes `done`
  only after your explicit approval, per the working loop in CLAUDE.md. One task, then stop.
- **RAC-N** is the acceptance criterion for task N. The RAG project uses the **RAC-** prefix (RAG
  Acceptance Criterion) deliberately, so a criterion never collides with the orchestration
  engine's `AC-N` scheme when the two projects are cross-referenced side by side. SPEC.md defines
  no pre-numbered acceptance criteria; each RAC here is grounded in the SPEC.md section 12
  checkpoints and the plan.md section 4 verification checkpoints.
- **Verify** commands run through `uv run` only (never a bare `python` / `pytest` / `pip`).
- **Gate rows** are branch points from plan.md section 2, not tasks; they decide what gets built
  next.
- **Grounding:** every Build task's step 2 (per the working loop) queries the `framework` graph
  for the exact symbol and signature before any library call is written. The "grounded surface"
  noted per task is the confirmed Phase 0 target, not a substitute for that per-task query.

## ARD registration (part of every capability's definition of done)

Two registrations, not one, and they must not be conflated.

Internal registration (T6) registers a built capability by its FR-C name with its contract, in
`src/rag_wright/capabilities/registry.py`. Its consumer is the Model Context Protocol (MCP) skill
surface (T31).

ARD registration is the Agentic Resource Discovery manifest that lets the GraphWright compiler
discover and bind the capability when it compiles the ingestion and query graphs from the
Orchestration Spec. Its consumer is the compiler's discovery and gap-analysis gate, not this repo.
The manifest is a `*.json` file conforming to GraphWright's `RegistryEntry` schema (GraphWright
T0.6 / ADR-0005), placed under the real registry root (its location is fixed on the GraphWright
side), then loaded and validated by `RegistryStore` (GraphWright T6.1). This is not a compiler step
run here; it is authoring a file the already-built store consumes.

One key, two consumers: the FR-C / FR-I / FR-Q name is both the internal registry key and the
anchor of the ARD manifest's URN, so the gap report and the registry speak one vocabulary. The T6
seam emits the manifest skeleton from what registration already knows (URN, kind, contract-derived
response bounds); each capability then authors what cannot be derived, above all the representative
queries, since those are the task-bearing field discovery ranks on (GraphWright T6.4), and a
capability with weak ones is one the compiler will not find.

Definition of done. Every task that builds a capability **the compiler discovers by representative
query** — one of the SPEC section 5 canonical slugs — carries the final acceptance bullet appended
below and is `done` only when its ARD manifest is authored (representative queries filled) and loads
under `RegistryStore(root)` with no `RegistryLoadError`. This covers the RLM skill (T15) and each
FR-C / FR-Q capability that has a section-5 slug (T16, T17, T19, T21, T22, T23, T23b, T24, T26, T27,
T28, T29). It does **not** cover the contracts, the seams (T11, T13), **the seam-bound ingestion
pipeline steps that have no section-5 slug — chunk write (T20, FR-I.3) and graph storage (T25,
FR-I.4): they bind the store seam and are wired into the ingestion graph by the compiler, not
discovered by query, so they register nothing and author no manifest**, the foundation tests, the
corpus and eval tasks, the T18 escalation path (it extends the T17 capability, not a new one), the
T30 caching optimization, or the T31 MCP surface.

**The rule (three categories — a canonical slug and an ARD manifest are NOT the same thing; the test is
"is it discovered by query at compile time," per GraphWright's RegistryStore verification, SPEC §5):**

1. **Query-discovered capabilities** — canonical slug **and** an ARD manifest. The compiler discovers
   them by representative query and binds them into the graphs. The FR-C / FR-Q capabilities, now **14**
   after the FR-C.9 split into `generation` + `vision_to_text` (ADR-0014).
2. **Seam-bound pipeline nodes** — **no** slug, **no** manifest (they bind the store seam, wired by the
   compiler, not discovered): chunk write (T20, FR-I.3), graph storage (T25, FR-I.4).
3. **Foundation derivations** — a canonical slug (a real capability with a contract + internal registry
   entry) but **no** manifest, because they run **before** compilation and their output is an input to
   the graph, not a node the compiler binds: `ontology_registry_derivation` (FR-C.8, T8).

A task carries the ARD bullet iff it is category 1. Stated once here, repeated per task so the working
loop enforces it.

---

## Status board

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T1 | Shared identifier contracts (`chunk_id`, `entity_id`) | 3 Contracts | FR-S.2, FR-S.3 | done | - |
| T2 | Provenance and confidence contracts | 3 Contracts | FR-S.4 | done | T1 |
| T3 | Chunk record contract | 3 Contracts | FR-I.3, FR-S.1 | done | T1, T2 |
| T4 | Ontology and extraction-target models | 3 Contracts | FR-C.8 | done | T1, T2 |
| T5 | Graph extraction contract (real OpenIE extension seam) | 3 Contracts | FR-C.6, FR-I.4 | done | T2, T4 |
| T6 | Capability registration seam | 3 Contracts | FR-S.5, contract use | done | T3 |
| T7 | CUAD + EDGAR corpus acquisition and subset | 4 Foundations | Phase 0 | done | - |
| T8 | Ontology and registry derivation | 4 Foundations | FR-C.8, FR-C.7 | done | T4, T7 |
| T9 | Golden eval harness + CUAD-annotation archetype sets | 4 Foundations | §12 | done | T7 |
| T10 | EDGAR-derived relational + multi-hop question construction | 4 Foundations | §12, §8 | done | T7, T9 |
| T11 | Model-profile seam (DeepSeek V4 Pro first for structured-under-reasoning) | 4 Foundations | assumption 2, tech stack | done | T6 |
| T12 | A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR) | 4 Foundations | FR-C.6 dep, risk 3 | done | T11 |
| T13 | Store seam + ArcadeDB schema and hybrid indexes | 4 Foundations | FR-S.1, FR-S.5 | done | T3, T6 |
| T14 | A-T1 ArcadeDB `vector.fuse` hybrid foundation test | 4 Foundations | FR-C.3 dep, risk 1 | done | T13 |
| T15 | RLM skill + machinery (recursive dynamic sub-agents; B′) | 4 Build RLM | FR-C.10 | done (rebuild) | ADR-0015/0016/0017/0018 |
| T16 | Parsing (Docling) | 4 Build write | FR-C.1 | done | T3 |
| T17 | RLM chunking (LLM semantic boundary discovery) | 4 Build write | FR-I.1 | done (rebuild) | T15, T16, T11, ADR-0019 |
| T18 | Small-to-large chunking escalation | 4 Build write | FR-I.2 | deferred (GATE-1) | **GATE-1**, T17 |
| T19 | Embedding (BGE-M3; concurrent + backpressure) | 4 Build write | FR-C.2, FR-I.3, FR-I.6 | done | T16 |
| T20 | Chunk write + incremental upsert (content-hash gated) | 4 Build write | FR-I.3, FR-I.5 | done | T13, T17, T19 |
| **GATE-1** | **RLM chunker A/B go / no-go** | 4 Build | §12, plan §2 | run: keep RLM, defer T18 → GATE-2 | T17, T19, T9 |
| T21 | Hybrid search (server-side RRF, metadata filters) | 4 Build read | FR-C.3, FR-Q.1 | done | T14, T20 |
| T22 | Reranking (cross-encoder precision gate) | 4 Build read | FR-C.4, FR-Q.2 | capability done; RAC-22 b2 open | T21 |
| **GATE-2** | **Recall-bar: ArcadeDB hybrid vs LanceDB fallback** | 4 Build | FR-S.5, plan §2 | run 1 not adjudicated (ADR-0011); keep ArcadeDB | T21, T22, T9, T10 |
| T-CHK | RLM chunker degenerate-split fix (T17 bug; ask-first) | 4 Build write | FR-I.1 | done | T17 |
| T-SUM | Concurrent summarization (T17 enhancement, FR-I.6 pattern) | 4 Build write | FR-I.1, FR-I.6 | done | T17 |
| T-DISP | `requires_dynamic_dispatch` typed flag on `skill_runtime` (RLM-rebuild groundwork) | 4 Build RLM | FR-C.10, ADR-0017 | done | ADR-0015, ADR-0017 |
| T33 | ACORD retrieval half of the JOINT query-graph golden-eval (ingest + recall bar + chunk_read) | 4 Foundations | §12, FR-Q | retrieval half EXECUTED + baseline measured (recall@50 0.379, grounded bar 0.667 not met — known limitation, T41); graded conjunction run handed to GraphWright | T21, T22, T38, T40 |
| T40 | Chunk-text sidecar: persist full chunk text keyed by chunk_id at ingest, same content-hash gate as the index | 4 Build write | FR-I.3 | done | T17, T19, T20 |
| T38 | `chunk_read` governed capability (rehydrate chunk_ids → chunks-with-text) — reads the T40 sidecar | 4 Build read | FR-Q | done | T40 |
| T39 | Extraction-depth grading (cited-but-thin) — needs answer-span ground truth ACORD lacks | 5 Integrate | §12 | todo (logged follow-on; not this milestone) | T33 |
| T41 | Retrieval-quality: baseline-with-diagnosis recorded (0.38, query-representation gap, graph leg doesn't help); composition-experiment backlog logged (LLM reranker / category label-retrieval / base-pool sizing) | 5 Integrate | FR-C.3 | investigation concluded → parked as post-integration backlog | T33 |
| T42 | RLM/synthesis latent-hardening pass: enforce worker query+slice threading + non-stub multi-worker split test + messy-fixture discipline | 5 Integrate | FR-Q.5 | done (query→worker prompt; slice→dispatch, both files drift-synced; partition + messy tests) | T28, T38 |
| T43 | Emit `capabilityInterface` typed I/O on authored ARD manifests (GraphWright ADR-0030) — schema + author path + declare the 7 (5 + graph_query + generation) interfaces | 5 Integrate | FR-C, ARD reg | done (ADR-0021) — nominal typing; `CapabilityInterface` + `NOMINAL_TYPE_VOCABULARY` (6 names) in ard.py, threaded through author path, 7 manifests declared; 14 tests, suite 426+27. **Contract closed:** fusion out retyped `fused_chunk`→`chunk_id` (GraphWright's call, `fusion→chunk_read` type-checks); all 15 manifests re-emitted to `~/.air/registry` | T6, T38, ADR-0005 |
| T44 | Govern the remaining capabilities: `capabilityInterface` on the 7 ingestion→graph caps (GraphWright ADR-0030, closes the ungoverned force-fit hole) | 5 Integrate | FR-C, ARD reg | done (ADR-0021 addendum) — vocabulary extended 6→14; parsing/rlm_chunking/embedding/graph_extraction/entity_disambiguation/entity_resolution/vision_to_text declared; `rlm_method` stays ungoverned (required skill, no data node); GraphWright mirrored the 14 + confirmed; all 14 governed manifests re-emitted. 8 tests, suite 434+27 | T43 |
| T34 | Document update/upsert: on doc change, delete a document's chunks + graph nodes + index entries, then re-chunk and re-insert | 5 Integrate | FR-I.5 | todo (finding) | T17, T20, T25 |
| T35 | Concurrent-batch ingestion throughput design (KI-1 correctness floor already always-on) | 5 Integrate | OQ8, ADR-0020 | todo (throughput design; floor landed) | T17, T28 |
| T36 | Working-set via runtime tool `tools.workingSet()` (not message-embedded JSON) + T17/T28 re-validation + skill rename | 5 Integrate | FR-C.10, FR-I.1, FR-Q.5 | done | T17, T28 |
| T37 | Recursion completeness + load faithfulness: coverage tail + load assertion in the skill's interpreter WORKFLOW | 5 Integrate | FR-C.10, FR-Q.5, FR-I.1 | done (both sides; GraphWright node bind_run 6/6 full-load-and-full-depth) | T36 |
| T23 | Graph extraction (contract + spaCy NER; concurrent + backpressure) | 4 Build graph | FR-C.6, FR-I.4, FR-I.6 | done | T5, T8, T16 |
| T23b | Mention disambiguation and canonicalization (normalize, reject, cluster) | 4 Build graph | FR-C.7 | done | T23 |
| T24 | Entity resolution (closed-world to EDGAR CIK) | 4 Build graph | FR-C.7 | done | T8, T23b |
| T25 | Graph storage (nodes/edges carry `chunk_id`, gated) | 4 Build graph | FR-I.4, FR-I.5 | done | T13, T23, T24 |
| T26 | Graph query (cited `chunk_id`s, `entity_id`s, confidence) | 4 Build graph | FR-C.5, FR-Q.3 | done | T25 |
| T27 | Fusion (union/dedup on `chunk_id`, capped) | 4 Build graph | FR-Q.4 | done | T22, T26 |
| T28 | RLM synthesis (recursive descent + kept _reduce ascent) | 4 Build RLM | FR-Q.5 | done (rebuild) | T15, T27, ADR-0019 |
| T29 | Answer generator (grounded, cited, abstains) + vision-to-text | 4 Build RLM | FR-C.9, FR-Q.6 | done | T11, T27 |
| T30 | Prefix + result caching | 4 Build RLM | §13 P3, §16.7 | todo | T28, T29 |
| T31 | MCP skill surface (governed skills over MCP) | 5 Integrate | FR-S.5 | todo | T6, T22, T26 |
| T32 | End-to-end scenarios + per-source ablation | 5 Integrate | §12, §15 | todo | T29, T31 |

**FR-K — Embedding-free OKF navigation (experimental, gated). SHELVED 2026-07-23 (ADR-0025):** the recall
investigation showed the *realizable ranking* recall (~0.38, method-invariant, matching the recorded two-leg
0.379) governs, not the *reachability* ceiling (0.776) FR-K rested on. Superseded by **FR-R** below. Committed
T45-T50 stay in history; not built further. Original spec retained for the record.

Corpus-neutral capability; ACORD is the first validation corpus. First execution slice is T45-T48 → GATE-3a (the
per-corpus reachability kill-switch); the whole program is specified now but built behind that gate. Detail
entries below.**

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T45 | ACORD gold-chunk labels for reachability scoring (extends T9/T33 eval assets) | 5 Integrate | §12, FR-K.8 | done | T33 |
| T46 | OKF bundle compile: chunk-only, category tree via corpus-appropriate classifier, LLM one-line descriptions (no re-chunk; sidecar-fed) | 5 Integrate | FR-K.1, FR-K.2, FR-K.4 | done | T40, T45 |
| T47 | Reachability analyzer + signpost ablation (deterministic, model-free) | 5 Integrate | FR-K.8 | done — **ceiling proven: any-gold 0.930, per-gold recall ceiling 0.776 > bar 0.667 > baseline 0.379** (ADR-0022 addendum 2) | T46 |
| T48 | Category label-retrieval control arm (T41 backlog item, run as the control) | 5 Integrate | FR-C.3 | done — **control recall@50 0.134 (containment 0.895): category is a strong bucketer, useless localizer → OKF traversal's description-localization is JUSTIFIED** | T46 |
| **GATE-3a** | **Reachability ceiling (per-corpus kill-switch): is gold reachable through signposts, and does the coarse-label control already capture the lift?** | 5 Integrate | §13, FR-K.8 | pending | T47, T48 |
| T49 | Cross-linking from measured clause-relation structure | 5 Integrate | FR-K.3 | done — **embedding-free lexical links RAISE the ceiling: per-gold 0.776→0.845, all-gold 0.491→0.614; Option-2 kNN NOT needed** | T46, T41 |
| T50 | `okf_navigate` traversal capability (interpreter + PTC + dynamic sub-agents) | 5 Integrate | FR-K.5, FR-K.6 | **in progress — WORKS: model writes the workflow from an okf_navigate Skill, 11/11 on tpb. (b1 DONE, ADR-0024) reader judging is now a Python-parallel `judgeBodies` PTC tool (asyncio fan-out via the seam, not a sub-agent — the interpreter is single-JS-engine per ADR-0020): tpb 163s→110s, LoL >15min→~5.8min, structured output back on the seam. Big-category coverage (LoL 0/12: budget 50 of 489 fills in index order) is the (b2) target. Pending: (b2) deep-hierarchy clustering, hard-query generalization, ARD manifest** | T46, T47, T49, T35 |
| T51 | Single-query trace-and-iterate harness (the tuning loop) | 5 Integrate | FR-K.8, §12 | todo (behind GATE-3a) | T47, T50 |
| **GATE-3** | **FR-K graduate or remove: OKF traversal vs the category-label control** | 5 Integrate | §13, §15 | pending | T47, T50, T51, T48 |
| T52 | Bundle lifecycle: incremental recompile, update, delete (shares the T40 gate) | 5 Integrate | FR-K.7 | todo (post-GATE-3) | GATE-3, T34 |
| T53 | Strategy memory: store and reuse verified navigation strategies | 5 Integrate | FR-K.9 | todo (post-GATE-3) | GATE-3, T50, T51 |
| T54 | graph_extraction ontology extension (not FR-K): make `ClauseCategory` cover non-CUAD corpora — T8-derived or proposal-seam, NOT a crosswalk (can't invent missing coverage) | 4 Build graph | FR-C.6, FR-C.8 | deferred (ask-first; data-model change) | T8, T23, ADR-0022 |

**FR-R — Function/Property retrieval (ADR-0025; supersedes FR-K).** Solve the ACORD recall bar the way a
paralegal does: every query = a **FUNCTION** (clause type) + a **PROPERTY** (qualifier). Operative-span index →
local function classifier → property graph → rerank for the fuzzy tail; spans + property graph in ArcadeDB
(clauses stay source-of-truth in the clause OKF bundle). Capability-half; pipeline composition is GraphWright's.
Built behind **GATE-R** (clears the 0.667 recall bar / beats the 0.379 two-leg baseline).

| ID | Task | Phase | FR | Status | Dep |
|---|---|---|---|---|---|
| T55 | Operative-span segmenter: re-chunk each clause → operative spans (enumeration/semicolon markers + spaCy legal-sentence, deterministic; byte-faithful reconstruction, size floor), each pointing to its parent clause. Output = `Span` records for ArcadeDB (text, `parent_chunk_id` + parent OKF path, function slot, dense/sparse emb slot) | 5 Integrate | FR-Q, §GATE-2 | **in progress — SEGMENTER DONE (`spans/segment.py`: deterministic, byte-faithful tiling, enumeration/sentence split with abbrev/section-ref guards + sub-floor merge; 9 tests; real-clause spot-check splits run-ons into operatives). NEXT: ArcadeDB `Span` type (schema + dense/sparse indexes + upsert + span hybrid_search + parent pointer)** | T13, T40, ADR-0025 |
| T56 | Local FUNCTION classifier over spans: single-label {41 CUAD `ClauseCategory` + NONE}; linear head on frozen embeddings (escalate to LegalBERT only if F1 lags); tag + index function-bearing spans | 5 Integrate | FR-C.3, FR-Q | todo | T55, T8 |
| T57 | PROPERTY schema (draft from the 57 ACORD query patterns; **schema review gate**) + targeted property extractor (reuse Extractor seam / DeepSeek) → **new** property graph (clause node ↔ typed property edges; reuse graph_storage / entity_resolution, NOT the generic entity graph) | 5 Integrate | FR-C.6, FR-C.7 | todo (schema review gate) | T55, T25, T24 |
| T58 | Query decomposition (→ function + property) + in-store retrieval: span hybrid-search filtered by function → property graph query → parent clauses | 5 Integrate | FR-Q.3, FR-Q.4 | todo | T56, T57 |
| T59 | Reranker for the fuzzy/comparative/novel property tail: `BGEReranker` default; LLM-rerank via the seam optional | 5 Integrate | FR-Q | todo | T58, T29 |
| **GATE-R** | **Does function + property + rerank clear the recall bar? recall@50 ≥ 0.667 (+ nDCG@10 diagnostic) vs the 0.379 two-leg baseline, ablated by stage (`eval/acord_retrieval`)** | 5 Integrate | §13, §GATE-2 | pending | T55-T59 |

Extras (§3.2: multi-vector, typed-functional RLM, CLIP embedder, canonical skeleton, domain
LoRA) are **out of scope / ask-first** and are not scheduled here.

---

## Phase 3 — Contracts (gates all Build tasks)

Contracts live in `src/rag_wright/contracts/` (Pydantic v2, per ADR-0001). These double as the
registered capability contracts (contract use). Identifier schemes are load-bearing and fixed
here; changing either later is ask-first (FR-S.2, FR-S.3; SPEC §14).

### Task T1: Shared identifier contracts (`chunk_id`, `entity_id`)

**Description:** Define the two load-bearing identifiers as Pydantic models. `chunk_id` is
computed from source-document identifier + chunk index + content hash; `entity_id` is the
canonical registry identifier. Fixed before anything is built (FR-S.2, FR-S.3).

**RAC-1:**
- [x] `chunk_id` is computed deterministically from `(source_doc_id, chunk_index, content_hash)`;
  identical inputs yield an identical `chunk_id` (determinism test). `ChunkId.of()` computes the
  content hash (SHA-256); `source_doc_id` is constrained to `[A-Za-z0-9._-]` so the `:`-delimited
  `.value` is parse/match-safe for provenance and citation.
- [x] `entity_id` is a canonical-registry identifier type (EDGAR CIK shaped, per ADR-0002). The
  contract is **strict**: canonical 10-digit zero-padded form only; raw EDGAR forms (CIK-prefixed,
  unpadded, integer) are rejected and normalized upstream at the T8 registry loader.
- [x] Both models are frozen/validated; malformed inputs are rejected.

**Verification:** `uv run pytest tests/contracts/test_identifiers.py` — 40 passed.

**Dependencies:** None. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/identifiers.py`, `tests/contracts/test_identifiers.py`
**Note:** Load-bearing; scheme change is ask-first (SPEC §14). `EntityId` normalization deferred to
T8 by review decision (contract stays strict; the loader owns the world's mess).

### Task T2: Provenance and confidence contracts

**Description:** Every stored unit carries provenance (source document + chunk for text); every
graph-derived fact carries a confidence tag (FR-S.4).

**RAC-2:**
- [x] Text-bearing models require source-document + chunk provenance. `Provenance` carries an
  explicit `source_doc_id` plus `chunk_id`; `Provenance.of(chunk_id)` derives the source doc.
- [x] Graph-fact model requires `confidence ∈ {EXTRACTED, INFERRED, AMBIGUOUS}`; any other value
  is rejected. `GraphFact` base carries `provenance` + `confidence` (the FR-I.4 node/edge unit).

**Verification:** `uv run pytest tests/contracts/test_provenance.py` — 18 passed.

**Dependencies:** T1 (uses `ChunkId`). **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/provenance.py`, `tests/contracts/test_provenance.py`
**Note:** Enforces "no claim without a citation" downstream (FR-Q.6). `source_doc_id` is kept
explicit (not a derived property, unlike a purity collapse) by review decision: it enables
store-level source filtering (FR-Q.1), decouples provenance from the `chunk_id` string format, and
avoids parsing the identifier — safe because the consistency validator fires on every construction
and deserialization path (raw / `model_validate` / `model_validate_json`), closing drift.

### Task T3: Chunk record contract

**Description:** The chunk record contract holds `chunk_id`, the summary, the dense summary
vector, the sparse full-text vector, keywords, entities, and source metadata (FR-I.3), all in one
record per chunk for the single store (FR-S.1).

**RAC-3:**
- [x] `ChunkRecord` carries `chunk_id`, summary, dense summary vector, sparse full-text vector,
  keywords, `entity_mentions`, source metadata, and validates each. No raw full-text field (FR-I.3
  enumerates summary + vectors + metadata; full text lives in the parse manifest keyed by
  `chunk_id`, FR-I.1).
- [x] Vector field shapes/types are declared so the store schema (T13) can bind them:
  `BGE_M3_DENSE_DIM = 1024` (single authoritative dense dimension), `dense_vector` fixed length +
  finite; `sparse_vector: dict[int, float]` (store-bindable token-id index -> weight) coerced from
  BGE-M3's string keys, non-integer keys rejected (not dropped); `source_metadata` constrained to
  JSON scalars for filterability (FR-Q.1).

**Verification:** `uv run pytest tests/contracts/test_chunk_record.py` — 27 passed.

**Dependencies:** T1, T2. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/chunk.py`, `tests/contracts/test_chunk_record.py`
**Note:** By review decision the extracted-entities field is named `entity_mentions` (unresolved
surface forms for retrieval metadata), NOT `entities` — it must not be joined to graph `entity_id`s
(resolution is FR-C.7 / T24); the name kills the fragmentation ambiguity at the seam.

### Task T4: Ontology and extraction-target models

**Description:** Pydantic ontology models for the 41 CUAD clause categories plus party and entity
types (FR-C.8, §16.2, ADR-0002). This is the contract shape; the derivation that populates it is
T8.

**RAC-4:**
- [x] The 41 CUAD clause categories (`ClauseCategory`, authoritative, exactly 41) plus party/entity
  types (`EntityType`) and relationship types (`RelationshipType`) are expressed as Pydantic models.
  T4 owns ontology **structure**; **T8 is the authority for entity/relationship membership** (a
  provisional seed ships now: `ORGANIZATION`/`PERSON`, `CONTRACTS_WITH`/`AFFILIATE_OF`).
- [x] A fact that does not conform to the ontology is rejected (enum-typed fields on `EntityNode`,
  `ClauseFact`, `RelationshipFact`; off-ontology category/type/relationship raises).

**Verification:** `uv run pytest tests/contracts/test_ontology.py` — 19 passed.

**Dependencies:** T1, T2. **Scope:** M. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/ontology.py`, `tests/contracts/test_ontology.py`
**Note:** Review decisions — (1) ship the provisional entity/relationship seed, T8 finalizes
membership from the Data Catalog; (2) `RelationshipFact` is **directed** (`source_ref -> target_ref`)
so T8 can add directed corporate-hierarchy types without reopening it; (3) endpoints are
**pre-resolution mention refs**, self-loop rejected at the ref level here, while the post-resolution
same-`entity_id` self-loop check belongs with entity resolution (T24); (4) a `CONTRACTS_WITH` fact
references its agreement via required provenance (`source_doc_id`), making shared-party multi-hop
answerable.
**Files:** `src/rag_wright/contracts/ontology.py`, `tests/contracts/test_ontology.py`

### Task T5: Graph extraction contract (real OpenIE extension seam)

**Description:** The contract graph extraction (T23) emits: conforms to the ontology (T4), carries
`chunk_id` provenance and a confidence tag (T2), and exposes the OpenIE path as a **real seam in
the contract**, an extractor interface that additional extractors bind, **not a comment or a
TODO**. Adding the deferred Open Information Extraction (OpenIE) path later plugs a new extractor
into the seam without reopening or editing the capability (plan §1 note, FR-C.6, FR-I.4).

**RAC-5:**
- [x] Extraction result conforms to the ontology and carries originating `chunk_id` + confidence.
  `ExtractionResult` bundles `ClauseFact`/`RelationshipFact` (T4, provenance+confidence) plus typed
  `EntityMention`s, and a validator anchors every fact to the result's `chunk_id` (FR-I.4).
- [x] The contract defines an extractor interface (a real abstraction the capability iterates
  over): `Extractor` is a `@runtime_checkable Protocol`; `run_extractors(...)` iterates a list.
  A new extractor (OpenIE) is added by appending to that list — no change to the seam.
- [x] A stub second extractor is bound behind the seam in test and its output validates: two stubs
  (`_ClauseStub` ships-first, `_OpenIEStub` the future path) merge with no change to
  `run_extractors`/`ExtractionResult`, proving the seam is load-bearing.

**Verification:** `uv run pytest tests/contracts/test_extraction.py` — 11 passed.

**Dependencies:** T2, T4. **Scope:** S. **Status:** done (commit pending).
**Files:** `src/rag_wright/contracts/extraction.py`, `tests/contracts/test_extraction.py`
**Note:** Adding the OpenIE dependency itself is ask-first (ADR-0001, risk 10); the seam makes it a
plug-in, not a reopen. Review decisions: keep typed `EntityMention` (captures standalone entities,
distinct from T3's retrieval-metadata mentions); `Protocol` over `abc.ABC` (conform-without-inherit)
— `runtime_checkable` isinstance is a presence check only, output conformance is enforced by
`ExtractionResult` validation. `EntityMention.text` and relationship `source_ref`/`target_ref` are
the same surface-form notion → T24 resolves both channels as one stream (see T24).

### Task T6: Capability registration seam

**Description:** A registry seam where each built capability registers under its FR-C name with
its contract (contract use, FR-S.5). Registration is what the MCP skill surface (T31) later
exposes. The same seam also emits the capability's Agentic Resource Discovery (ARD) manifest
skeleton (see "ARD registration"), so the internal registry and the ARD manifest share the FR-C
name as their one key and stay coherent.

**RAC-6:**
- [x] A capability registers by canonical slug with its contract; lookup by slug returns it.
- [x] Registering an unknown (non-canonical, per SPEC §5) or duplicate name is rejected; unknown
  lookup raises. The name is the cross-spec join key — a canonical slug (`hybrid_search`, never
  `fr-c-3`), enforced against the mirrored slug set.
- [x] Registration emits an ARD manifest skeleton conforming to the mirrored GraphWright
  `RegistryEntry` schema (URN `urn:air:dreamai.io:rag_wright:<slug>`, kind, callable response bounds),
  leaving the authored fields (representative queries 2-5, trust attestations) for the capability
  task. The skeleton is a DRAFT that cannot validate as a `RegistryEntry`; only
  `ManifestSkeleton.author(...)` yields the loadable entry, kept out of the glob'd path until then.

**Verification:** `uv run pytest tests/capabilities/test_registry.py` — 27 passed.

**Dependencies:** T3 (uses a contract type). **Scope:** M. **Status:** done (commit pending).
**Files:** `src/rag_wright/capabilities/ard.py`, `src/rag_wright/capabilities/registry.py`,
`tests/capabilities/test_registry.py`, `docs/adr/0003-ard-registration-and-capability-kinds.md`
**Note:** Review decisions in **ADR-0003** — emit JSON, no `graphwright` dependency; the ADR-0005
schema is a shared wire-format contract mirrored by deliberate duplication (cross-repo coordination
point) with a RAG-side conformance test; kind is explicit per capability by the binding rule
(`mcp_tool` = MCP boundary, `function` = in-process node, `agent_skill` = loaded knowledge); the
canonical slug set is mirrored from SPEC §5 and enforced; `generation` (FR-C.9) is one capability.
**Note:** The ARD manifest is the interface to the GraphWright compiler's discovery; the internal
registry is the interface to the MCP surface (T31). One key (the FR-C name), two consumers.

### Checkpoint: Contracts complete
- [x] All contract tests pass (142 in the suite). Identifier schemes fixed and approved. Phase 3
  (Contracts) done: T1 identifiers, T2 provenance/confidence, T3 chunk record, T4 ontology, T5
  extraction seam, T6 registration seam + ARD mirror (ADR-0003). Ready to build on them.
- Forward notes carried into Phase 4: T8 registry loader normalizes messy EDGAR CIK forms to
  canonical `EntityId` (from T1 strictness); T24 resolves relationship refs and standalone
  `EntityMention`s as one mention stream and dedupes the post-resolution self-loop (from T4/T5).

---

## Phase 4 — Foundations (spec Phase 0 / early Phase 1)

After contracts, the foundation seams (model, store, ontology/registry, RLM skill) and the
corpus + eval build are largely parallel (plan §2). Two foundation tests prove the risky seams
before capabilities depend on them.

### Task T7: CUAD + EDGAR corpus acquisition and subset

**Description:** Acquire and subset CUAD to roughly 100–150 contracts (~100MB), deliberately
including some scanned filings so the Docling parse and vision-to-text path is exercised; pull the
SEC EDGAR entity data. Confirm the Creative Commons Attribution 4.0 (CC BY 4.0) license at
download (SPEC §13 Phase 0, ADR-0002).

**RAC-7 (acquisition; folds `docs/Corpus_Acquisition.md`):**
- [x] Two acquisition scripts pull CUAD and EDGAR; all outputs land under gitignored `data/`,
  nothing corpus-sized committed. CC BY 4.0 confirmed and recorded (attribution in `LICENSE.txt`).
- [x] CUAD: three artifacts from one pinned snapshot (`zenodo:4595826`, more reproducible than
  mixing sources) — contract PDFs, master-clauses CSV (41-category annotations, feeds T9), SQuAD
  JSON. 510 contracts; pool 501 (9 `Filename`↔PDF mismatches skipped).
- [x] Subset is a **deliberate recorded filter after the full pull** (150 contracts, 28 agreement
  types, 29.3MB), coverage-driven — **not a first-N slice** — with multi-party (121) and
  shared-party (16 groups / 38 contracts) coverage. **CUAD ships no image-only PDFs**, so 10
  selected contracts are deterministically **rasterized to true image-only PDFs** (poppler +
  Pillow, verified 0 text chars) to exercise the vision-to-text path (Option A). Manifest + criteria
  + `scanned.json` recorded → reproducible.
- [x] EDGAR: `company_tickers.json` seed (10,415 companies; feeds T8) + submissions for the
  subset's proposed parties. Fetch sends a monitored User-Agent and respects **≤10 req/s aggregate
  sliding-window + durable disk cache** — verified: cold run 24 network calls, warm re-run **0**.
- [x] Mechanical name→CIK proposals are **structurally UNVERIFIED** (explicit `status` field +
  separate `data/edgar/proposed/` location). 23/323 resolved (high-precision), 300 unresolved
  (expected private/variant entities; carries `UNRESOLVED_NOTE`). Human verification is T10's job.

**Verification:** `uv run python scripts/acquire_cuad.py --check` and
`uv run python scripts/acquire_edgar.py --check` (coverage + license printed, no writes);
`uv run pytest tests/corpus/` — 37 passed.

**Dependencies:** None. **Scope:** L. **Status:** done.
**Files:** `scripts/acquire_cuad.py`, `scripts/acquire_edgar.py`,
`src/rag_wright/corpus/{selection,http,edgar,cuad}.py`, `tests/corpus/`, `data/` (gitignored).
**Note:** Data task, not a capability. Gates T8, T9, T10. Acquisition spec:
`docs/Corpus_Acquisition.md`. Core committed `f1e8820`; CLIs + live run complete the task. Real-data
finding: CUAD has no image-only PDFs (resolved by rasterizing 10); conservative matching is
high-precision/low-recall by design (see T10 unresolved-set note).

### Task T8: Ontology and registry derivation

**Description:** Populate the ontology models (T4) from CUAD's 41 clause categories, and build the
entity registry from EDGAR CIK data with CIKs as the canonical `entity_id`s. Resolution is
closed-world against this registry (FR-C.8, FR-C.7, §16.2/§16.3, ADR-0002). The matching strategy
(§16.3) is decided at T24; this task delivers the registry it resolves against.

**RAC-8:**
- [x] The T4 ontology is reconciled against the real CUAD `master_clauses.csv` columns
  (`reconcile_clause_categories`, case-insensitive + answer-spacing-tolerant). **Live: 41/41
  matched, 0 missing, 0 extra** — the 41 categories are confirmed against real data with no ontology
  change; the reconciliation yields a CSV-column→category map for T9.
- [x] The entity registry is built from EDGAR CIK data; CIKs are the canonical `entity_id`s. **Live:
  8,006 companies from the T7 seed (10,415 ticker-rows collapse to unique CIKs), 0 invalid skipped.**
- [x] **T8 owns EDGAR normalization** — the registry loader **reuses `corpus.edgar.normalize_cik`**
  (the single CIK→`EntityId` point, zero-pad-to-10 + strict T1 validate), so it cannot drift. The
  fragmentation test passes: messy forms (`int`, unpadded, `CIK`-prefixed) all land on the canonical
  `EntityId`; an invalid CIK row is skipped, not invented.
- [x] The registry is seeded from the T7 `company_tickers.json`; submissions→former-name aliases are
  supported (`aliases_by_cik`) and wired at T24 where resolution consumes submissions.
- [x] Closed-world lookup works: `resolve` returns the canonical id for a known name/ticker/alias
  (case/punctuation-insensitive) and `None` for an unknown surface form (never fabricated).

**Verification:** `uv run pytest tests/ontology/test_derivation.py` — 10 passed.

**Dependencies:** T4, T7. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/ontology/derive.py`, `src/rag_wright/ontology/registry.py`,
`tests/ontology/test_derivation.py`
**Note:** The `EntityId` contract (T1) is strict canonical-only; this loader is the single place
raw EDGAR forms enter and get normalized. Keep normalization here, not in the contract.
**ARD category — foundation derivation (GraphWright RegistryStore verification, ADR-0014 taxonomy):**
`ontology_registry_derivation` (FR-C.8) is a **canonical slug with NO ARD manifest**. It is not
seam-bound; it is a real capability with a contract and an internal registry entry, but it runs **before**
compilation and its output (the ontology + registry) is an **input** to the graph, not a node the
compiler discovers by query — so it authors no manifest. Category 3 in the three-category ARD rule above.

### Task T9: Golden eval harness + CUAD-annotation archetype sets

**Description:** Build the pytest evaluation harness and the CUAD-derived golden sets: CUAD expert
clause annotations give ground truth for the exact/lexical archetype, the semantic archetype, and
clause-finding answer-and-citation questions (SPEC §12, plan §4). The harness measures recall@k
per archetype per leg (text and graph measured separately). The relational and multi-hop archetype
is built as its own task, T10, deliberately not folded in here.

**RAC-9:**
- [x] Golden sets exist for the exact/lexical, semantic, and clause-finding archetypes, grounded in
  CUAD expert annotations (SQuAD span answers). **Live: 2,032 subset questions** (exact_lexical 893,
  clause_finding 737, semantic 402), pinned to `zenodo:4595826` + the 150-contract subset in the
  build record → gitignored `data/eval/golden.json`, rebuilt by `uv run python -m eval.build_golden`.
- [x] Harness measures recall@k per archetype, text and graph legs separately (`evaluate` returns
  `archetype/leg` means; `retrieve` injected so the plumbing is proven before capabilities exist).
- [x] Harness runs green on a tiny fixture (recall correctness, per-leg separation, builder spread +
  skipping, map covers all 41 categories).

**Verification:** `uv run pytest eval/test_harness.py` — 5 passed (now in the default suite, 194).

**Dependencies:** T7. **Scope:** M. **Status:** done.
**Files:** `eval/harness.py`, `eval/golden.py`, `eval/build_golden.py`, `eval/test_harness.py`,
`data/eval/golden.json` (gitignored). **testpaths** now includes `eval`.
**Note:** Everything after Phase 0 is measured against this; feeds GATE-1 and GATE-2. The
`ARCHETYPE_BY_CATEGORY` map is a **testable hypothesis** to revisit once per-category/per-leg recall
is observable (review: Termination for Convenience → clause-finding; Parties kept lexical as a known
borderline; Post-Termination Services + Competitive Restriction Exception flagged revisit).

### Task T10: EDGAR-derived relational + multi-hop question construction

**Description:** Construct the relational and multi-hop golden questions **from the EDGAR
party-and-entity graph** (SPEC §12, §8, ADR-0002). This is a distinct foundation task on purpose:
CUAD alone is single-document clause extraction and under-tests the archetype the graph layer
exists for, and a multi-hop set is the easiest thing to accidentally under-build into a set of
single-document questions. Making it visible and separate is what makes the graph leg and the
per-source ablation (T32) measurable at all.

**RAC-10:**
- [x] Questions require traversal across the EDGAR party-and-entity graph (relational and
  genuine multi-hop, not single-document lookups), with ground-truth answer entities and the
  `entity_id`/`chunk_id` evidence path recorded. **Live: 19 questions** — 16 one-hop (hub's full
  co-party set) + 3 two-hop; `entity_paths` records the entity_id evidence chain per answer, chunk_id
  path resolved at eval time (same deferral as CUAD `relevant_ids`).
- [x] **The name→CIK links are human-verified, not auto-generated** (folds
  `docs/Corpus_Acquisition.md`): linking a contract's parties to CIKs is itself the entity-resolution
  problem (FR-C.7), so building the answer key by fuzzy matching and then testing fuzzy matching
  against it is circular. **Only the human-verified `resolution` fields (45/45: 28 CIK + 17 PRIVATE)
  enter the set** — `build_relational` reads ground truth, never re-matches. CIK filers and
  verified-PRIVATE entities are **both first-class** (axis = verified-vs-unverified); variant
  spellings collapse by resolved identity; distinct subs keep separate keys (ScanSource vs ScanSource
  Latin America); SKIP excluded.
- [x] The set is registered as its own archetype split in the harness (T9), separate from the
  CUAD-annotation sets. `RelationalQuestion.to_golden()` → `Archetype.RELATIONAL`; `evaluate()`
  reports `relational/text` and `relational/graph`, each leg separate.
- [x] Coverage is enough to measure the graph leg's recall and its per-source ablation loss (T32),
  not a token handful. **Honest property recorded** (`public-filer-centric, multi-hop-modest`): the
  corpus is star-shaped, so 16 one-hop but only 3 genuine 2-hop hubs — modest *by the corpus*, not by
  under-building. Made visible by measurement (`make-load-bearing-work-a-visible-task`), not hidden.

**Verification:** `uv run pytest eval/test_multihop_set.py` — 6 passed (full suite 233). Rebuild the
set: `uv run python -m eval.multihop`.

**Dependencies:** T7, T9. **Scope:** M. **Status:** done.
**Files:** `eval/multihop.py`, `eval/golden/relational/set.json` (**committed** verified fixture),
`eval/test_multihop_set.py`, `docs/adr/0005-relational-golden-set.md`.
**Note:** The entire reason the ArcadeDB graph layer exists is measured here. Do not fold into T9.
The built set is committed (verified answer keys, the only durable copy in git); the gitignored
triage file `data/edgar/verification_set.json` stays out. Decisions in **ADR-0005** (identity key,
`PRIVATE:<key>` sentinel keeps `EntityId` strict, commit-the-fixture rationale).
**Unresolved-set note (from T7):** the T7 name→CIK proposals are conservative (normalized
conformed-name only), so the UNRESOLVED set is *expected* to contain real entities — private
companies/individuals (absent from EDGAR) and name variants (subsidiaries, former names, DBAs). The
verifier must not read "unresolved" as "not an entity"; graph entity coverage is public-filer-centric
(FR-C.7), a property to keep in mind when interpreting multi-hop results. See
`rag_wright.corpus.edgar.UNRESOLVED_NOTE`.

### Task T11: Model-profile seam (DeepSeek V4 Pro first for structured-under-reasoning)

**Description:** One construction point that builds the model client (`langchain_openai.ChatOpenAI`
per ADR-0001) keyed by model id, with the structured-output method (default `function_calling`)
and an optional structured-only extra body carried in a per-model profile config, never a
hardcoded provider/model flag in call sites. Free-text and reasoning calls are unaffected
(assumption 2, tech stack, CLAUDE.md standing rule). Model priority is a **seam config decision,
not capability code**:

- **DeepSeek V4 Pro is the default profile for structured-output-under-reasoning calls** (contract
  extraction, grader-style, and synthesis calls), because it handles reasoning together with
  forced structured output more reliably, which is exactly the failure surface this system leans
  on. Its empirically-determined structured-output method and any structured-only extra body are
  recorded in the seam's ADR (T12).
- **Qwen 3.7 Plus is kept as a secondary profile** for the same call class.
- **The Gemma 4 class model remains the default** for the local-deployment mode and general
  generation.

**RAC-11:**
- [x] A profile keyed by model id supplies structured-output method + optional structured-only
  extra body; the extra body applies only to the forced-structured call. `ModelProfile{model_id,
  structured_method (default function_calling), structured_extra_body}`; the seam threads
  `extra_body` through `with_structured_output(schema, method=..., extra_body=...)` so it binds to
  the structured runnable only — a test asserts it reaches the structured call but never the base
  client's constructor (free-text / reasoning unaffected).
- [x] The structured-under-reasoning default resolves to DeepSeek V4 Pro; Qwen 3.7 Plus is a
  selectable secondary; Gemma 4 class is the local-mode / general-generation default. All of this
  lives in profile config, and no provider/model-specific flag appears in any call site. `ModelRole`
  → id via `model_for`, each env-overridable (`RAG_MODEL_*`); an unregistered model gets the safe
  default profile so no call site special-cases a model.
- [x] `with_structured_output(...)` is reached only through the seam (`seam.build_structured`; the
  base `seam.build_model` carries no structured flag or `extra_body`).

**Verification:** `uv run pytest tests/models/test_profile_seam.py` — 9 passed (unit, fake client, no
network; full suite 242).

**Dependencies:** T6. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/models/profiles.py`, `src/rag_wright/models/seam.py`,
`tests/models/test_profile_seam.py`
**Note:** Priorities live here, never in capability code (CLAUDE.md standing rule). The model slugs
(`deepseek/deepseek-v4-pro`, `qwen/qwen-3.7-plus`, `google/gemma-4-class`) are documented,
env-overridable placeholders and DeepSeek's `structured_extra_body` ships `None`: the exact slug and
the empirical thinking-disable `extra_body` are confirmed against a live call and recorded in a dated
ADR at **T12** (no unconfirmed provider flag baked in, per the standing rule). Grounded against
`langchain_openai.chat_models.base` (ADR-0001): `with_structured_output` forwards kwargs into the
tool binding, which is what makes `extra_body` structured-call-only.

### Task T12: A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR)

**Description:** Prove the model-profile seam against an actual forced-schema call **on DeepSeek V4
Pro**, the model the seam now prioritizes for structured-output-under-reasoning and the model
extraction (T23) will use, the same failure surface open models hit in thinking mode (plan A-T2,
risk 3). Learn and record the working profile (method, structured-only extra body) in a dated ADR
before extraction depends on it.

**RAC-12:**
- [x] A forced-schema call on DeepSeek V4 Pro returns a valid contract instance through the seam.
  Live: `seam.build_structured("deepseek/deepseek-v4-pro", _PartyExtraction).invoke(...)` returned a
  valid Pydantic instance; the Qwen secondary passes too (via its profile's `extra_body`).
- [x] The working profile (method + structured-only extra body) for DeepSeek V4 Pro is recorded in
  a dated ADR; Qwen 3.7 Plus is noted as the secondary profile. **ADR-0006** (dated 2026-07-07):
  DeepSeek = `function_calling`, no `extra_body` (honors the forced tool call while reasoning);
  Qwen = `function_calling` + `{"reasoning":{"enabled":false}}` (bare call fails risk-3 in thinking
  mode). Slugs corrected from T11 placeholders.

**Verification:** `uv run pytest tests/foundation/test_model_seam_structured.py -m model` — 2 passed
(primary + secondary; requires OpenRouter access, marked `model` so it is opt-in; default suite skips
it, 242 passed + 2 skipped).

**Dependencies:** T11. **Scope:** S. **Status:** done.
**Files:** `tests/foundation/test_model_seam_structured.py`, `docs/adr/0006-model-profile.md`
(renumbered — `0003` is taken by ARD), `src/rag_wright/models/profiles.py` (empirical slugs +
`extra_body`), `conftest.py` + `pyproject.toml` (opt-in `model`/`store` markers, `.env` load).
**Note:** Needs OpenRouter access (`.env`). Validated the profile on the prioritized model and
reproduced risk 3 live on the secondary, proving the seam's structured-only `extra_body` is
load-bearing. De-risks FR-C.6 before it is built.

### Task T13: Store seam + ArcadeDB schema and hybrid indexes

**Description:** Define the query-skill seam (so the store is swappable, FR-S.5) and create the
ArcadeDB schema with the dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` indexes via the
`arcadedb_python` `SyncClient` / `DatabaseDao` (FR-S.1, §16.4). Resolves the schema/index
specifics open question at this task.

**RAC-13:**
- [x] The query-skill seam interface is defined; the ArcadeDB implementation sits behind it.
  `store/seam.py` `Store` (runtime_checkable Protocol, semantic not SQL: `ensure_schema`,
  `type_names`, `property_names`, `index_names`, `ping`, `close`); `ArcadeDBStore` binds it.
- [x] Schema created with dense `LSM_VECTOR` (dims=`BGE_M3_DENSE_DIM` 1024, COSINE) and sparse
  `LSM_SPARSE_VECTOR` indexes; chunk records and graph nodes carry `chunk_id`. `Chunk` (chunk_id
  UNIQUE, source_doc_id, dense, sparse_indices, sparse_weights) + `Entity` (entity_id UNIQUE,
  chunk_id). **Empirical (26.7.2): sparse index needs two parallel arrays not a map** → T3
  `sparse_vector: dict[int,float]` decomposed at the store boundary (T20). Idempotent by schema
  introspection (ArcadeDB rejects `IF NOT EXISTS` here). ADR-0007.
- [x] A second stub implementation can bind the same seam (proves swappability for the LanceDB
  fallback path). `_InMemoryStore` in the test satisfies `isinstance(..., Store)` and the schema
  surface.

**Verification:** `uv run pytest tests/store/test_arcadedb_schema.py -m store` — 3 passed (live
ArcadeDB, opt-in); hermetic stub tests run in the default suite (244 passed + 5 skipped). Local run:
`docs/ArcadeDB_Local.md`.

**Dependencies:** T3, T6. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/store/seam.py`, `src/rag_wright/store/arcadedb.py`,
`tests/store/test_arcadedb_schema.py`, `docs/adr/0007-arcadedb-store-schema.md`,
`docs/ArcadeDB_Local.md`, `.env.example`, `conftest.py` (marker-gate fix).
**Note:** Grounded against `arcadedb_python` 0.4.0 (`SyncClient`/`DatabaseDao`) and the live 26.7.2
server (driver is v0.x, risk 2). `vector.fuse` confirmed present (de-risks GATE-2; proven end-to-end
at T14). Query-side vector-search function name pinned at T14/T21.

### Task T14: A-T1 ArcadeDB `vector.fuse` hybrid foundation test

**Description:** Write a few chunk records and run a `vector.fuse` Reciprocal Rank Fusion (RRF)
hybrid query end to end, confirming the sparse index and server-side fusion behave as documented
(plan A-T1, risk 1). Surfaces early whether we are heading for the LanceDB fallback.

**RAC-14:**
- [x] A few records written; a `vector.fuse` RRF hybrid query returns a sensible fused ranking
  honoring a metadata filter. Live: `SELECT expand(vector.fuse(vector.neighbors('Chunk[dense]',...),
  vector.sparseNeighbors('Chunk[sparse_indices,sparse_weights]',...), {fusion:'RRF'}))` fuses the
  dense-leg and sparse-leg winners above the weak record; metadata filter via
  `SELECT ... FROM (<fuse>) WHERE source_doc_id='docA'` returns only that source.
- [x] The result (works as documented / lean-toward-fallback) is recorded as an early signal for
  GATE-2. **Signal: works as documented, no lean toward the LanceDB fallback** (ADR-0007 A-T1
  section). Early A-T1 signal only; the GATE-2 decision is measured on the golden set at T21/T22.

**Verification:** `uv run pytest tests/foundation/test_arcadedb_hybrid.py -m store` — 2 passed (live
ArcadeDB 26.7.1, opt-in); default suite 244 passed + 7 skipped.

**Dependencies:** T13. **Scope:** S. **Status:** done.
**Files:** `tests/foundation/test_arcadedb_hybrid.py`, `docs/adr/0007-arcadedb-store-schema.md`
(A-T1 result). Adds no src code — a foundation probe over the real T13 schema via the grounded
arcadedb-python DAO.
**Note:** Early de-risk of the recall-bar gate; not the gate itself. Hybrid SQL grounded against the
official ArcadeDB docs (the Python API does not wrap `vector.fuse`/`sparseNeighbors`) + confirmed on
the live server.

### Task T-DISP: `requires_dynamic_dispatch` typed flag on `skill_runtime` (RLM-rebuild groundwork)

**Description:** The recursive-RLM rebuild (ADR-0015) reopens T15/T17/T28 to make RLM interpreter +
dynamic sub-agents. Its dynamic dispatch is prompt-triggered by the interpreter (langchain-quickjs's
"workflow" word), a **silent under-performance** if it fails to fire. Grounding the pinned
`langchain-quickjs==0.3.2` proved a skill **cannot** inject that trigger from its own authored content
(the trigger reads the *user's request*, and the skill content lands in the *system message*) — so the
requirement is declared as a **typed flag** `requires_dynamic_dispatch: bool` on `skill_runtime`, and
GraphWright's runtime (not our wire contract) owns the magic word (ADR-0017). This is the ARD-contract
prerequisite that gates T15's reimplementation.

**Acceptance:**
- [x] `SkillRuntime.requires_dynamic_dispatch: bool = False` mirrored on `ard.py`; a validator rejects
  `requires_dynamic_dispatch=True` with `needs_interpreter=False` (dynamic dispatch is exposed by the
  interpreter), kept a **distinct** field (not collapsed into `needs_interpreter`).
- [x] `agent_skill`-only, like the rest of `skill_runtime` (a `function` manifest carrying it fails
  `RegistryEntry` validation).
- [x] Populated `true` on the three RLM specs (`rlm_method`, `rlm_chunking`, `rlm_synthesis`); the wire
  form carries `skillRuntime.requiresDynamicDispatch: true` (camelCase).
- [x] Grounding recorded: **self-dispatch confirmed** on `deepagents==0.6.12` — dispatch is name-based
  (`subagents_by_name`/`subagent_graphs` name-keyed dicts, `deepagents/middleware/subagents.py:585,588`)
  and a self-referential `rlm_decomposer` roster constructs with **no infinite recursion**. So
  `task(subagentType="rlm_decomposer")` self-dispatch (ADR-0015 Q2) is structurally sound. Last pre-T15
  grounding item cleared.

**Verification:** `uv run ruff check` clean; `uv run pytest tests/capabilities/test_manifests.py` —
22 passed; full suite **373 passed + 26 skipped**. Wire form verified:
`skillRuntime: {needsInterpreter, rlm, grantedSubagents: [], requiresDynamicDispatch: True}`.

**Dependencies:** ADR-0015, ADR-0017. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/capabilities/ard.py`, `src/rag_wright/capabilities/manifests.py`,
`tests/capabilities/test_manifests.py`, `docs/adr/0017-dynamic-dispatch-trigger-as-typed-flag.md`.
**Note:** `grantedSubagents` stays `[]` until the T15/T17/T28 rebuild lands (populated then to
`["rlm_decomposer","rlm_slice_worker"]`, ADR-0015). **T15 stays held** until both this flag (landed here)
and GraphWright's applier (translating the flag into the interpreter's trigger) are in place (ADR-0017).

### Task T15 (REOPENED 2026-07-15, B′ rebuild): RLM skill + machinery (recursive dynamic sub-agents)

**Description:** The ADR-0015/0016 rebuild turns RLM from interpreter-plus-model-calls into interpreter
+ dynamic sub-agents. T15 delivers the reusable machinery T17/T28 apply: the rewritten `SKILL.md`, the
two Deep Agents sub-agent configs (`rlm_decomposer`, `rlm_slice_worker`), the interpreter-driven
recursive `decompose()` workflow, and the ADR-0016 fail-if-absent tests. **Design B′** (ADR-0015 Q2,
corrected): the recursion lives in the interpreter, which re-dispatches a *fresh* `rlm_decomposer` per
level; a self-referential agent is **not constructible** on `deepagents==0.6.12` (eager roster compile).

**RAC-15 (rebuild):**
- [x] `SKILL.md` teaches the B′ method: load the working set into the interpreter, write a recursive
  `decompose()` workflow that dispatches a fresh decomposer per level + a worker per leaf, combine in
  code. States the workflow trigger is declared (`requiresDynamicDispatch`) and applied by the runtime.
- [x] Machinery in `src/rag_wright/skills/rlm/agent.py`: `RLM_DECOMPOSER`/`RLM_SLICE_WORKER`/
  `GRANTED_SUBAGENTS`, `decomposer_config`, `slice_worker_config` (per-slice tools + skills),
  `RLM_WORKFLOW_JS` (the shipped recursive descent), and `build_rlm_agent(...)` assembling
  `create_deep_agent` + `CodeInterpreterMiddleware`, models through the profile seam (never a string id).
- [x] **Four ADR-0016 fail-if-absent tests, written first and confirmed red against pre-rebuild code**
  (no `build_rlm_agent` entrypoint), then green — driving the REAL machinery + shipped `RLM_WORKFLOW_JS`
  with scripted fake models (hermetic, genuine `task()` dispatch): (1) recursion-forced — the eval result
  shows `maxSplitDepth>=1`, `leafCount==3` (only reachable via two levels), and the decomposer fired at
  >1 depth; a **teeth** test proves a flat one-level workflow fails it; (2) a leaf worker invokes a tool
  mid-handling; (3) a leaf worker loads a skill (source present in its context); (4) code-driven fan-out —
  dispatches carry the parent `eval_id`; a **teeth** test proves sequential (top-level `task`) dispatch is
  rejected.
- [x] **Opt-in real-model smoke test** (`@pytest.mark.model`) proving what the fakes cannot: a REAL
  orchestrator, given the method, recurses on the decomposer's output. It surfaced a defect **here**: the
  method wired as a lazy `skills=` source was **never read**, so the model flatten-and-hardcoded the
  split. Fix (ADR-0018): load the method into the orchestrator's **system prompt**; strengthen SKILL.md
  (explicit recursion mandate + anti-pattern). Test redesigned with an **opaque** working set whose leaves
  only the decomposer reveals, so full leaf coverage proves derivation (not a lowered bar). **10/10**
  live runs on `google/gemma-4-31b-it` after the fix (was 0-1 before).
- [ ] `grantedSubagents` population to `["rlm_decomposer","rlm_slice_worker"]` + re-emit is deferred to
  after the full rebuild (T17, T28) lands, per the sequencing directive (manifests currently `[]`).

**Verification:** `uv run pytest tests/capabilities/test_rlm_method.py` — 6 passed + 1 skipped (the
`model` smoke test); `-m model` — 1 passed live (10/10 across the hit-rate harness). Red confirmed by
moving `agent.py` aside (ModuleNotFoundError). ruff clean. Full suite **379 passed + 26 skipped** (+1
opt-in). Deterministic chunk core (`_split_into_chunks`, `ChunkId`, gate, `Chunk`) and the `_reduce`
fan-in are **untouched** (T17/T28 preserve them as separate functions).

**Dependencies:** ADR-0015, ADR-0016, ADR-0017, ADR-0018. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/skills/rlm/SKILL.md`, `src/rag_wright/skills/rlm/agent.py`,
`tests/capabilities/test_rlm_method.py`, `tests/capabilities/_fixtures/rlm_probe_skill/SKILL.md`,
`docs/adr/0015-rlm-dynamic-subagents-and-granted-subagents.md` (Q2 correction),
`docs/adr/0018-rlm-method-is-the-orchestrator-system-prompt.md`.

### Task T15 (original, superseded by the rebuild above): RLM skill authoring (general method only)

**Description:** Author the RLM SKILL.md as the general divide-and-conquer method (load a working
set into an interpreter, slice and dispatch in code, synthesize). It has no testable behavior of
its own; the RLM chunking (T17) and RLM synthesis (T28) capabilities each apply it with their own
contract and tests (FR-C.10, plan §1 note).

**RAC-15:**
- [x] `SKILL.md` teaches the method (interpreter load, code-side slice/dispatch, synthesize) as
  authored software, not a build-tool feature. `src/rag_wright/skills/rlm/SKILL.md` (frontmatter +
  the three-step method, recursive; shows how RLM chunking and RLM synthesis each apply it).
- [x] It defers all determinism/boundary/gating behavior to the applying capabilities. Explicit
  "What this skill does NOT own" section: determinism, boundary validation, gating, model choice.
- [x] ARD-registered as an `agent_skill`: manifest authored via `ard.write_manifest` and **present in
  the shared root** (`~/.air/registry/rlm_method.json`, `urn:air:dreamai.io:rag_wright:rlm_method`,
  `application/ai-skill+md`). Mirror conformance tests green; the on-disk manifest re-validates as a
  `RegistryEntry`. Live `RegistryStore(root)` load is deferred to the GraphWright side (not done here).

**Verification:** Manual review of `SKILL.md`; `uv run pytest tests/capabilities/test_manifests.py`
— 3 passed (authoring + on-disk shape). Publish to the root: `uv run python scripts/publish_manifests.py`.
Full suite 251 passed + 7 skipped.

**Dependencies:** None. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/skills/rlm/SKILL.md`, `src/rag_wright/capabilities/manifests.py`
(reusable ARD-authoring seam, one `CapabilityManifest` per capability — extended each T15-T29 task),
`scripts/publish_manifests.py`, `tests/capabilities/test_manifests.py`.
**Note (cross-cutting, per the T15-T29 directive):** every capability authors its ARD manifest via
`ard.py` into the shared root under `urn:air:dreamai.io:rag_wright:<slug>` with the mirror conformance
test green and the manifest present in the root; no live `RegistryStore` load here (GraphWright side).

### Checkpoint: Foundations complete
- [ ] Contract, model-seam, and registry unit tests green. A-T1 and A-T2 run and their outcomes
  recorded (ADR for A-T2 on DeepSeek V4 Pro). Corpus + both eval sets (CUAD-annotation and
  EDGAR multi-hop) in place. Ready for the write-side.

---

## Phase 4 — Build, write-side (spec Phase 1)

> **FR-I.6 note (line redrawn at Phase 2 review).** FR-I.6 has three parts on two sides of the
> boundary. (1) **Model-tiering per task** (which model each capability uses) is a built-capability
> concern via the model-profile seam (T11) and the escalation task (T18). (2) **CPU-GPU
> decoupling, pooled inference, and backpressure** is a property of *how the GPU-calling
> capabilities are implemented* (poolable, non-blocking, backpressure at the capability boundary),
> so it is an acceptance criterion on the embedding (T19) and extraction (T23) tasks, built in now
> because it is cheap to build in and expensive to retrofit. (3) The **two run modes** (bulk takes
> the machine, background yields to serving) are orchestration and deployment, genuinely not a
> capability, and are out of scope here (compiler / deployment concern).

### Task T16: Parsing (Docling)

**Description:** Turn source documents (PDF, Office files, scans) into a clean structured
representation (reading order, headings, sections, tables, OCR), parsed once and reused by
chunking, embedding, and extraction (FR-C.1). Grounded surface: `docling` `DocumentConverter`.

**RAC-16:**
- [x] A CUAD PDF and a scanned filing both parse into the structured representation (headings,
  sections, tables; OCR text present for the scan). **Live (`-m parse`, 2 passed, 203s):** the
  smallest text-layer CUAD contract parsed to headings/sections/markdown; the image-only scanned
  NETGEAR filing OCR'd to text (RapidOCR). `DocumentConverter` behind a `Parser` seam →
  `DoclingDocument`.
- [x] The parsed result is cached/reusable so it is parsed once. Content-hash gated: the
  `DoclingDocument` is cached (`save_as_json`/`load_from_json`) keyed by source content hash;
  unchanged content is a cache hit, changed content re-parses (proven hermetically via a
  call-counting stub `Parser`).
- [x] Registered under FR-C.1. `register_parsing` → `parsing`, kind `function`, contract
  `ParsedDocument` (source_doc_id reuses T1's citation-safe charset).
- [x] ARD-registered: manifest authored + **present in the shared root**
  (`~/.air/registry/parsing.json`, `urn:air:dreamai.io:rag_wright:parsing`, kind `function` with
  response bounds); mirror conformance green; re-validates as a `RegistryEntry`. Live `RegistryStore`
  load / discovery is the GraphWright-side step (not done here).

**Verification:** `uv run pytest tests/capabilities/test_parsing.py` (6 hermetic passed); live:
`... -m parse` (2 passed); manifest: `tests/capabilities/test_manifests.py`. Full suite 259 passed +
9 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T3. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/parsing.py`, `tests/capabilities/test_parsing.py`,
`src/rag_wright/capabilities/manifests.py` (+`parsing` spec), `tests/capabilities/test_manifests.py`
(scaling test over all specs), `pyproject.toml` + `conftest.py` (`parse` opt-in marker).

### Task T17 (REOPENED 2026-07-15, rebuild): RLM chunking = LLM semantic boundary discovery

**Description:** The B′ rebuild makes chunking what the SPEC requires: **LLM-driven semantic boundary
discovery, mandatory, no fixed-size chunking ever**. A strong model (STRUCTURED_REASONING / deepseek-v4-pro)
explores the parsed document via the T15 machinery (`build_rlm_agent` + a `peek` tool), and returns
semantically coherent boundary **spans over the document's items**. Reproducibility is moot (ingestion
chunks once and persists; only a document change re-chunks — task T34): the value is a good boundary, not a
repeatable one. The layer **after** boundaries are chosen is deterministic-given-boundaries and lives in a
separate function (`_finalize_chunks`): join each span, enforce the cap, apply the **T-CHK floor + merge +
near-empty rejection** (an LLM can just as easily emit a boundary around a lone heading), compute
`chunk_id`, validate. Summaries stay the existing concurrent flat map. Recursion is available-when-warranted,
**not gated** for chunking (ADR-0019).

**RAC-17 (rebuild):**
- [x] Boundaries are **LLM-found and semantic, not fixed-size**: a `BoundaryDiscoverer` seam
  (`SeamBoundaryDiscoverer` via `build_rlm_agent`, deepseek-v4-pro), spans over `document.texts` items.
  Hermetic tests inject a stub discoverer; **live** test asserts a known coherent clause (items 3..6) is
  **not split across chunks** — boundary quality, the T15-opaque-proof analogue. Live: **4/4** (1 via
  pytest + 3 via the hit-rate harness) on deepseek-v4-pro, incl. a clean semantic partition
  `(0,1)(2,2)(3,6)(7,8)` isolating the clause. (Thinner than T15's 10/10; this test is load-bearing — see
  the coverage caveat below.)
- [x] **Per-slice tool use** in the exploration: the discoverer's `peek(index)` tool is invoked mid-handling
  (hermetic, fake-model-driven through the real machinery).
- [x] **id/gate/validation deterministic given the spans**: stable `chunk_id`s given the same spans;
  content-hash gate skips the re-chunk **and the LLM call**; partition validation rejects gaps/overlaps/short
  coverage. **T-CHK preserved over the discoverer's spans**: the floor + merge + near-empty rejection now
  fire in `_finalize_chunks`; the dense-header pathology (one span per item) coalesces, no near-empty chunk;
  the ASIANDRAGON regression fixture still passes over spans. Added a residual-rebalance so the floor holds
  even for a tiny trailing span.
- [x] **Not recursion-gated** (ADR-0019): recursion available-when-warranted, intrinsic to synthesis/T28.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` — 14 passed + 2 skipped (live);
`-m model` coherent-clause **passed** (+ reliability re-runs). ruff clean. Full suite **379 passed + 26
skipped**. `_split_into_chunks` (the old header heuristic) removed; its floor/merge logic preserved in
`_finalize_chunks`. Added `tools=` (orchestrator tools) to `build_rlm_agent` for the `peek` tool.

**COVERAGE CAVEAT (deliberate, recorded):** the hermetic suite proves the *mechanism* (spans → correct
join, cap, T-CHK floor/near-empty, ids, gate, partition validation) but **asserts no boundary quality** —
that a coherent unit is kept whole is model judgment and cannot be checked against a fake. Boundary quality
is guarded **only** by the opt-in live test (`-m model`). So: **CI does not cover boundary quality**, and
`-m model` must be run before trusting any change that touches boundary discovery or the SKILL/prompt.
Removed the two old pure-heuristic tests (subsection-stays, major-boundary) — they asserted the old
deterministic heuristic's specific choices; that decision is now the LLM's and is covered only by the live
coherent-clause test. This is a real shift in what CI catches; not silent.

**Dependencies:** T15, T16, T11, ADR-0019. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py`, `src/rag_wright/skills/rlm/agent.py` (+`tools=`),
`tests/capabilities/test_rlm_chunking.py`, `docs/adr/0019-recursion-is-optional-for-chunking-required-for-synthesis.md`.
**Finding logged:** T34 (document update/upsert path does not exist).

### Task T17 (original, superseded by the rebuild above): RLM chunking (deterministic, content-hash gated)

**Description:** Read the whole parsed document through an interpreter (not bounded by a context
window) using the RLM skill (T15), splitting along topic/section/chapter boundaries into
semantically coherent chunks (variable size, capped ~20,000 tokens), writing a summary per chunk,
a manifest per document, and stable `chunk_id`s. Deterministic and reliable: temperature zero or
structured output, boundary validation, and a content-hash gate so an unchanged document is not
re-chunked (FR-I.1).

**RAC-17:**
- [x] Same document in yields identical chunk boundaries and `chunk_id`s across runs. Boundaries are
  code-deterministic (from the parsed section structure + the token cap); `chunk_id` via
  `ChunkId.of` (canonical `<src>:<idx>:<64hex>`). Summaries deterministic via structured-output /
  temperature-zero. Test: chunk twice → identical `ChunkManifest`.
- [x] Boundary validation runs; chunks capped at ~20,000 tokens; a summary + manifest produced.
  `_validate_boundaries` (uniqueness, sequential index, non-empty, ≤ cap); over-cap items hard-split;
  per-chunk summary; per-document `ChunkManifest`. **Cap-accumulation bug (joined length vs summed
  per-item estimates) found by the live end-to-end run and fixed; hermetic regression added.**
- [x] The content-hash gate skips an unchanged document (no re-chunk). Manifest cached by content
  hash; unchanged → cache hit (stub summarizer call-count proves no new summaries).
- [x] Registered under the RLM chunking capability. `register_rlm_chunking` → `rlm_chunking`,
  `agent_skill`, contract `ChunkManifest`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/rlm_chunking.json`,
  `agent_skill`, `requires: ['rlm_method']`); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (6 hermetic passed); live:
`-m model` (summarizer) and `-m "parse and model"` (**end-to-end real parse + 6 real DeepSeek Flash
summaries, all chunks ≤ cap; 41s**). Full suite 266 passed + 10 skipped.

**Dependencies:** T15, T16, T11. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py`, `tests/capabilities/test_rlm_chunking.py`,
`src/rag_wright/models/profiles.py` (new `SUMMARIZATION` role → DeepSeek V4 Flash, FR-I.6 tiering),
`src/rag_wright/capabilities/manifests.py` (rlm_chunking spec, requires rlm_method).
**Note:** Resolves chunking-skill internals (§16.1). Determinism is testable (risk 4). The real-doc
end-to-end run is the level at which the cap bug surfaced — hermetic stubs did not reach it.

### Task T18: Small-to-large chunking escalation

**Description:** A less reliable small chunking model handles the bulk for throughput; any
document that fails boundary validation is re-run on a larger model (FR-I.2). **Gated by GATE-1:**
built only if the RLM chunker beats the baseline. If not, this and the elaborate chunking
apparatus are dropped (plan §2).

**RAC-18:**
- [ ] The bulk runs on the small model; a document failing boundary validation is re-run on the
  larger model and passes or is dead-lettered.
- [ ] Model tiers are chosen through the model-profile seam (no hardcoded model flag).

**Verification:** `uv run pytest tests/capabilities/test_chunking_escalation.py`

**Dependencies:** GATE-1, T17. **Scope:** M.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (escalation path),
`tests/capabilities/test_chunking_escalation.py`
**Note:** Conditional — do not build before GATE-1 clears. This is the FR-I.6 model-tiering slice.
**Deferred (GATE-1, 2026-07-09):** the dense-only A/B did not justify the escalation apparatus, so
T18 is not built now; the RLM chunker is kept and the earns-its-cost decision moves to GATE-2/T22
(see the GATE-1 section). If the chunker is unproven there, make it optional rather than drop it.

### Task T19: Embedding (BGE-M3: dense over summary, sparse over full text)

**Description:** Produce dense vectors over summaries and native sparse vectors over full chunk
text, from one model (FR-C.2, FR-I.3). The dense-over-summary + sparse-over-full-text split is the
summary-miss mitigation (§12, risk 8). This is a GPU-calling capability, so it carries the FR-I.6
decoupling property. Grounded surface: `FlagEmbedding` `M3Embedder`.

**RAC-19:**
- [x] Dense vector over the summary; native sparse vector over the full chunk text, from the one
  BGE-M3 model. The sparse leg reads the chunk's full text (`Chunk.text`, from the T17 chunk/parse
  manifest keyed by `chunk_id`), not the record; the dense leg reads `Chunk.summary`. Routing
  verified hermetically (`dense_inputs==[summary]`, `sparse_inputs==[text]`); **live BGE-M3 (`-m
  embed`) produced real dense + native sparse.**
- [x] Output shapes match T3/T13: dense length `BGE_M3_DENSE_DIM` (1024); sparse `dict[int, float]`
  (BGE-M3's `Dict[str,float]` string keys converted to ints). Wrong dense dimension is rejected.
- [x] **FR-I.6 decoupling property:** `embed_chunks` is async/non-blocking; each encode runs through
  `asyncio.to_thread` (poolable boundary) bounded by a semaphore (backpressure). Concurrency test:
  in-flight encodes reach exactly `max_concurrency` (and `=1` is strictly serial); concurrent run is
  faster than serial.
- [x] Registered under FR-C.2. `register_embedding` → `embedding`, kind `function`, contract
  `ChunkEmbedding`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/embedding.json`,
  `urn:air:dreamai.io:rag_wright:embedding`); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_embedding.py` (6 hermetic passed); live:
`-m embed` (real BGE-M3, 1 passed, ~7min incl. model download). Full suite 273 passed + 12 skipped.

**Dependencies:** T16. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/embedding.py`, `tests/capabilities/test_embedding.py`,
`src/rag_wright/capabilities/manifests.py` (+embedding spec), `pyproject.toml` + `conftest.py`
(`embed` opt-in marker).
**Note:** FR-I.6 decoupling is built in here because it is cheap now, expensive to retrofit.
**GATE-1 is now runnable** (RLM chunker A/B needs T17 + T19 + T9 — all done); it is a human-gated
branch point that decides whether T18 (small→large escalation) is built.

### Task T20: Chunk write + incremental upsert (content-hash gated)

**Description:** Write chunk records to the store, upserting by `chunk_id`; make ingestion
incremental, resumable, and idempotent with per-document and per-chunk checkpoints and a
dead-letter queue for failed documents (FR-I.3, FR-I.5). Content-hash gating means an unchanged
document does effectively no work.

**RAC-20:**
- [x] A chunk record upserts by `chunk_id` (re-write of the same id updates, does not duplicate).
  ArcadeDB `UPDATE Chunk SET ... UPSERT WHERE chunk_id = ...`; the sparse vector is decomposed into
  the two parallel arrays at the store boundary (ADR-0007). **Live (`-m store`): re-writing a
  chunk_id updates in place, count stays 1.**
- [x] Re-running an unchanged document does effectively no work (content-hash gate). Per-document
  checkpoint keyed by content hash; a completed same-hash run returns `skipped` with no store writes.
- [x] A failed document lands in the dead-letter queue; a resumed run continues from checkpoints.
  Per-chunk checkpoints; a write exception dead-letters the doc; a retry skips already-written chunks
  and completes, clearing the dead-letter entry.
- **(No ARD bullet.)** Chunk write is a seam-bound ingestion pipeline step with no SPEC section-5
  slug (FR-I.3), so it registers nothing and authors no manifest — per the ARD-registration rule
  above (only query-discovered capabilities register). It reaches the store only through the T13
  `Store` seam (extended here with `upsert_chunk` / `get_chunk` / `chunk_count`).

**Verification:** `uv run pytest tests/capabilities/test_chunk_write.py` (6 hermetic passed);
`-m store` (real ArcadeDB upsert dedup/update, 1 passed). Full suite 279 passed + 13 skipped.

**Dependencies:** T13, T17, T19. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/chunk_write.py`, `tests/capabilities/test_chunk_write.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (write-side seam: `upsert_chunk`/`get_chunk`/`chunk_count`),
`tests/store/test_arcadedb_schema.py` (stub extended to the write-side seam).
**Note:** Capability-level idempotence and checkpoints (filesystem checkpoint + dead-letter dirs); the
store holds the records. The bulk-vs-background run modes (FR-I.6 part 3) are
deployment/orchestration, not built here. Not ARD-registered: a seam-bound pipeline step, no §5 slug
(see the ARD-registration rule).

### GATE-1: RLM chunker A/B go / no-go (branch point) — RUN; decision recorded

**Not a task.** A/B the RLM chunker (T17) against a simpler baseline chunker on the golden set
(T9): boundary quality + summary fidelity (§12, plan §2).
- **Beats baseline →** keep the RLM chunker; build the small-to-large escalation (T18) and the
  full apparatus.
- **Does not beat baseline →** drop T18 and the elaborate chunking apparatus; fall back to the
  simpler chunker; redirect the plan. Raise with the human before proceeding either way.

> **SUPERSEDED (2026-07-12, T-CHK):** these RLM numbers were measured on the buggy splitter (degenerate
> over-fragmentation + near-empty chunks, fixed in T-CHK). They do **not** change the GATE-1 decision
> (ADR-0009 keeps the RLM chunker regardless of any single-corpus result), but they must **not** be cited
> later as evidence about the chunking strategy. Any RLM-vs-baseline A/B after T-CHK requires a full
> re-ingest on clean chunks (the stored chunks are stale).

**Run (`eval/gate1_chunker_ab.py`, 2026-07-09) — dense-only proxy (T17 + T19 + T9; no store, no
sparse leg), 4 golden docs / 59 questions:**

| chunker | boundary quality | recall@1 | recall@3 | recall@5 | chunks/doc |
|---|---|---|---|---|---|
| RLM (T17, ~~superseded~~) | **0.920** | 0.200 | 0.536 | **0.799** | 4–7 |
| baseline (fixed window) | 0.886 | **0.322** | **0.562** | 0.685 | 13–17 |

Read honestly: RLM wins boundary quality; retrieval is **mixed and confounded** — RLM's recall@5
edge is inflated by having only ~5 chunks/doc (top-5 ≈ retrieve-everything), and on the fair metric
(recall@1) the baseline wins. Decisively, this proxy is **dense-over-summary only** and cannot see
the RLM design's core advantage — the **sparse-over-full-text leg** — which is exactly where the
baseline currently wins (literal matches). So GATE-1 is not the definitive test of the RLM chunker.

**Decision (human, 2026-07-09): KEEP the RLM chunker (not dropped). Do NOT build T18 now — defer.**
The elaborate small→large escalation is not justified on this dense-only proxy; the earns-its-cost
call is made at **GATE-2 / T22**, with the full hybrid pipeline. If the RLM chunker does not prove its
value there, **make it optional (a seam/config toggle), not dropped**. Proceed to T21.

**Eval caveat (standing rule, [[evals-in-depth-no-shortcuts]]):** this run used only the 4 shortest
well-covered docs to finish faster — a shortcut. GATE-2's eval must be in-depth and honest: a full or
genuinely representative sample (short AND long docs, all archetypes), never downscaled for time.

---

## Phase 4 — Build, read-side (spec Phase 1, FR-Q)

### Task T21: Hybrid search (server-side RRF, metadata filters)

**Description:** Fuse dense-over-summary and sparse-over-full-text results server-side by RRF in
ArcadeDB into one ranked candidate list, honoring metadata filters (FR-C.3, FR-Q.1). Grounded
surface: ArcadeDB `vector.fuse` (proven end to end at T14).

**RAC-21:**
- [x] A query returns one RRF-fused ranked candidate list from the dense and sparse legs. The query
  is embedded once (dense + sparse over the query text) via the T19 `Embedder` seam; both vectors go
  to the new semantic query-side seam method `Store.hybrid_search(dense, sparse, *, k, filters)`, which
  the ArcadeDB impl fuses server-side (`vector.fuse` RRF over the dense `vector.neighbors` and sparse
  `vector.sparseNeighbors` legs, the T14-proven SQL). Each leg fetched to `DEFAULT_CANDIDATE_POOL=100`,
  fused, cut to `k`. `hybrid_search(...)` returns a `HybridSearchResult` (RRF-ranked `Candidate`s).
  **Live (`-m store`): c1 (dense-leg winner) + c2 (sparse-leg winner) fuse to the top two; weak c3 is
  excluded.**
- [x] Metadata filters are honored. Equality filters applied post-fusion as a wrapping `WHERE` on the
  stored columns (proven at T14). **Live: a `source_doc_id='docA'` filter returns only docA candidates.**
- [x] recall@k per archetype is **measurable** on the golden set: `hybrid_search(query, ..., k)` gives
  the `retrieve(query, k)` shape the T9 harness injects. **The golden-set recall run itself is deferred
  to GATE-2/T22** (it must be in-depth, all archetypes, short AND long docs, per
  [[evals-in-depth-no-shortcuts]]); T21 delivers the measurable capability, not the gate.
- [x] Registered under FR-C.3. `register_hybrid_search` → `hybrid_search`, kind `function`, contract
  `HybridSearchResult`.
- [x] ARD-registered: manifest authored + **present in the shared root**
  (`~/.air/registry/hybrid_search.json`, `urn:air:dreamai.io:rag_wright:hybrid_search`, kind `function`
  with response bounds; representative queries authored); mirror conformance green; re-validates as a
  `RegistryEntry`. Live `RegistryStore(root)` load / `discover` is the GraphWright-side step (not here).

**Verification:** `uv run pytest tests/capabilities/test_hybrid_search.py` (5 hermetic passed); live:
`... -m store` (2 passed — real ArcadeDB server-side RRF + metadata filter); manifest:
`tests/capabilities/test_manifests.py`. Full suite 285 passed + 15 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T14, T20. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/hybrid_search.py`, `tests/capabilities/test_hybrid_search.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (query-side seam method + ArcadeDB RRF SQL, `_sql_literal`,
`DEFAULT_CANDIDATE_POOL`), `src/rag_wright/capabilities/manifests.py` (+`hybrid_search` spec),
`tests/store/test_arcadedb_schema.py` (stub extended to the query-side seam).
**Note:** No new ADR — reuses ADR-0007's ArcadeDB store/schema + the T14-grounded `vector.fuse` SQL.
Metadata filtering is **post-fusion** (filter the pooled fused list, then cut to `k`); the `pool=100`
per leg gives fusion and the filter room. The capability preserves the server's RRF order and does not
re-rank — that is the reranker's job (T22, the precision gate).

### Task T22: Reranking (cross-encoder precision gate)

**Description:** A cross-encoder in the BGE-reranker family reranks the candidate list and cuts it
to a top set before any expensive work, the precision gate before synthesis (FR-C.4, FR-Q.2).
Grounded surface: `FlagEmbedding` `FlagAutoReranker`.

**RAC-22:**
- [x] The candidate list is reranked and cut to a top-k set. `rerank(query, passages, *, reranker,
  top_k)` scores each `(query, passage)` pair via the BGE cross-encoder seam, sorts by score desc
  (stable — ties keep fused order), cuts to `top_k`. `Passage` in → `RerankResult` (ranked
  `ScoredCandidate`s). Pure query-side function; it does not fetch text (the graph wires summary vs
  full text). Live `-m rerank`: real `bge-reranker-v2-m3` ranks a relevant passage above an irrelevant.
- [ ] **PENDING valid queries.** Rerank improves precision@k over the raw fused list on the golden set.
  **GATE-2 run 1 could NOT adjudicate this** — rerank was ~neutral only because the relevant chunk was
  rarely in the candidate pool (CUAD-question query artifact, ADR-0011), which is not a rerank result.
  Closes when measured on ACORD's content-bearing queries (the ACORD task). **Do not check this bullet
  until then; T22 is committed with it explicitly open.**
- [x] Registered under FR-C.4. `register_reranking` → `reranking`, kind `function`, contract `RerankResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/reranking.json`,
  `urn:air:dreamai.io:rag_wright:reranking`, kind `function`); mirror conformance green; re-validates as
  a `RegistryEntry`. Live `RegistryStore(root)` load is the GraphWright-side step.

**Verification:** `uv run pytest tests/capabilities/test_reranking.py` (8 hermetic passed); live:
`... -m rerank` (1 passed, real cross-encoder). Full suite 294 passed + 16 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T21. **Scope:** M. **Status:** capability done; RAC-22 b2 open pending ACORD.
**Files:** `src/rag_wright/capabilities/reranking.py`, `tests/capabilities/test_reranking.py`,
`src/rag_wright/capabilities/manifests.py` (+`reranking` spec), `pyproject.toml` + `conftest.py`
(`rerank` opt-in marker; `transformers>=4.44.2,<5` pin — ADR-0010).
**Note:** transformers pinned <5 (ADR-0010): FlagEmbedding's reranker needs the `prepare_for_model` that
transformers 5.x removed. RAC-22 b2 is the GATE-2 rerank-precision measurement (see GATE-2 run 1 below).

### GATE-2: Recall-bar, ArcadeDB hybrid vs LanceDB fallback (branch point)

**Not a task.** GATE-2 carries **two** branch decisions, measured on the golden sets (T9 + T10), each
leg measured separately (§12, plan §2).

**(a) The store recall bar — ArcadeDB hybrid vs. LanceDB fallback.**
- **Meets the bar →** continue with ArcadeDB.
- **Underperforms →** substitute LanceDB for the retrieval leg behind the query-skill seam
  (FR-S.5) — the one eval-gated fallback, not a default. Raise with the human; the seam (T13)
  already makes the swap local to the store implementation.

**(b) The RLM chunker earns-its-cost call (deferred here from GATE-1).** The gate **measures** the RLM
chunker; it does **not get to delete** it. The only decision space is **keep-as-default vs. make-optional
(a seam/config toggle)** — never "drop" (ADR-0009). Rationale: RLM chunking's job is efficient semantic
*boundary preservation* (meaningful units — chapters/sections/sub-sections — not fixed-window cuts), and
meaningful chunks + the Knowledge Graph are two halves of one design for complex cross-part/temporal
reasoning grounded and cited (why ArcadeDB holds both in one store, FR-S.1). This is enterprise-grade RAG
for *any* corpus, not eval-chasing the current one: a capability that does not win on this corpus/query
mix is not thereby useless; semantic chunking's advantage is corpus- and query-type-dependent.
- **Consequence for the eval:** GATE-2 must be able to *see* RLM's advantage or it proves nothing about
  it — it must exercise cross-part / multi-hop / temporal queries (the T10 relational set exists for
  this) and account for KG synergy, on a full or genuinely representative sample (short AND long docs,
  all archetypes), never downscaled for time ([[evals-in-depth-no-shortcuts]]). GATE-1's 4-shortest-docs
  dense-only proxy is explicitly **not** sufficient for this call.
- Record the outcome (keep-default vs. optional) with its eval evidence, and raise with the human.

**GATE-2 run 1 (2026-07-11, `eval/gate2_hybrid_rerank.py`, 21-doc stratified sample) — NOT ADJUDICATED.**
The run used CUAD golden questions as cross-corpus retrieval queries and returned implausibly low recall
(exact_lexical recall@1 0.027; recall@10 0.13–0.29 across archetypes, both chunkers; rerank ~neutral).
Diagnosed before reporting: **CUAD is an extraction/classification benchmark, not a retrieval one** — its
questions/category labels share ~2–9% vocabulary with the answers, so the numbers measure a
query-formulation artifact, not the retriever (ADR-0011, [[cuad-not-a-retrieval-benchmark]]). GATE-1's
per-doc RLM recall@5 0.799 proves the pipeline retrieves correctly in a small haystack; the collapse is
the big-haystack × content-free-query combination, which hits both chunkers and any store equally.
Strict bookkeeping:
- **(a) store bar:** *not adjudicated on this run; no signal favoring LanceDB.* Keep ArcadeDB. **Not marked
  met** (we neither switch on an inconclusive measurement nor claim a bar we did not measure).
- **RAC-22 b2 (rerank precision):** *pending valid queries*, not checked.
- **(b) RLM earns-its-cost:** unchanged — post-graph GATE-2b (needs cross-part/multi-hop/temporal + KG),
  and blocked additionally by the T17 chunker bug below.
- **Genuinely proven (the run's real result):** the full read+ingest pipeline ran end to end at scale with
  zero crashes — 21 docs, both chunkers, parse → chunk → embed (dense+sparse) → ArcadeDB write →
  server-side RRF hybrid → BGE rerank, over 630–840 chunks. Real T16–T22 integration validation.
- **Fix path:** content-bearing queries via **ACORD** (T33, ask-first), and the **T17 chunker fix** (T-CHK)
  before any RLM-vs-baseline numbers.

**GATE-2 run 2 (2026-07-12, `--per-doc`, after T-CHK + T-SUM) — within-document signal only.** Re-ingest
on the fixed chunker: **0 summarizer fallbacks** (was 7 — confirms they were the split bug), RLM chunks
296 (was 632), no near-empty. Per-doc (within-document retrieval) recall is now believable (exact_lexical
R@1 ≈ 0.49 vs 0.027 cross-corpus in run 1 — confirms run 1's collapse was the CUAD-query artifact). RLM
edges baseline on recall in nearly every cell (reversing GATE-1's superseded signal), **but that edge is
partly confounded by RLM's lower chunk count** (fewer chunks → easier recall@k); on **precision@5 the two
are ~tied** (RLM .232/.136/.143 vs baseline .215/.122/.153). Rerank is marginally positive/neutral per-doc
(small haystack gives it little room). **Adjudicates nothing:** not the store bar (cross-corpus), not
RAC-22 b2 (rerank — stays pending ACORD), not the RLM keep-vs-optional call (GATE-2b, needs
cross-part/multi-hop/temporal + KG). Honest takeaway: on clean chunks RLM is **at least as good as
baseline**, not worse.

### Task T33: ACORD content-query retrieval eval (extends T9) — ask-first

**Description:** Ingest ACORD (Atticus Clause Retrieval Dataset: CC-BY-4.0, BEIR, 114 attorney-authored
queries, ~126k graded query-clause pairs, corpus of SEC/EDGAR + F500 ToS clauses — same family as ours,
distinct clause pool) into the store via the pipeline and run its expert queries through hybrid search +
rerank, scored against qrels. Supplies the content-bearing cross-corpus retrieval bar CUAD cannot
(ADR-0011). Adjudicates **GATE-2(a) store bar** and **RAC-22 b2 rerank precision**; does NOT exercise the
RLM chunker (pre-segmented clauses), so the RLM call stays at GATE-2b. **LLM-generated queries are rejected
as circular; if ACORD is unusable, return to the human before any alternative.**
**Status:** UNHELD / criteria LOCKED (2026-07-17). ACORD IS the **joint golden set**: one benchmark, one
ground truth (its qrels), two metrics. **T33 is RAG_Wright's retrieval half** — recall/nDCG over qrels on
the retrieve→rerank→fusion legs. GraphWright owns the **synthesis half** — citation-recall on
rehydrate→synthesis→answer. Both repos build toward this shared criteria; T39 (extraction-depth grading)
is a logged follow-on out of this milestone.

**Grading model:** **deepseek-v4-pro** (`STRUCTURED_REASONING`) pins the answer half — the project's
"larger model for quality-sensitive extraction" (ADR-0006), what the synthesis workers actually run and
what RAG_Wright's re-validations ran on. The joint eval pins ONE model (GraphWright's node had run
qwen3.7-plus/`_SECONDARY`); RAG_Wright owns the benchmark design and pins deepseek-v4-pro.

**Locked acceptance criteria (set before the run):**
- **Retrieval (RAG's half) — primary gate: `recall@50 ≥ 0.70`.** k=50 = the fused/reranked pool that
  feeds synthesis; this is the true system ceiling — a relevant clause that never reaches the evidence is
  unrecoverable downstream. `recall@10 ≥ 0.50` and `nDCG@10 ≥ 0.40` are reported, **not gated** (ranking
  diagnostics).
- **Answer (GraphWright's half) — `citation-recall ≥ 0.75`, conditioned on evidence reaching synthesis**
  (isolates the answer half without penalizing it for retrieval's misses).
- **PIN 1 — acceptance is the CONJUNCTION, with ordering: `recall@50 ≥ 0.70` AND `citation-recall ≥ 0.75`.**
  The retrieval gate is the **precondition** that makes citation-recall interpretable. Citation-recall
  alone is never a pass: conditioning on *arrived* evidence shrinks the denominator when retrieval
  underperforms, so a high conditioned value over a tiny arrived set (extreme: 1 clause arrived + cited →
  1.0) can look best exactly when retrieval failed. Retrieval passing = substantial evidence arrived = the
  citation-recall number is trustworthy.
- **PIN 2 — zero-arrival queries** (no relevant clause reached synthesis; citation-recall undefined) are
  **counted as retrieval failures under the recall gate, EXCLUDED from the citation-recall population** —
  not scored 1.0 or 0.0, not silently dropped. Pinned before the run because how the degenerate cases fold
  in affects the aggregate.
- **Pre-run-judgment caveat:** thresholds are calibrated to ACORD's difficulty (hard attorney clause
  retrieval), not measured achievability (T33 unbuilt). If a dry run shows them miscalibrated, recalibrate
  **before the graded run, never after** — preserves the "set before results" discipline.
- **PIN 3 — the conditioning assumed a passing retrieval bar; the product rule grounds the bar (2026-07-18).**
  This makes PIN 1's precondition explicit and resolves how the retrieval bar is set (supersedes the
  ungrounded `recall@50 ≥ 0.70`).
  - **Hidden assumption, now explicit:** conditioned citation-recall (denominator = relevant clauses that
    *reached synthesis*, not all relevant) only isolates the synthesis half *fairly when a substantial,
    representative evidence set arrives* — i.e. when retrieval clears its bar. Below the bar the arrived set
    is small and **biased toward easy-to-retrieve clauses**, so the conditioned number measures synthesis on
    an unrepresentative subset and is **not interpretable alone**.
  - **Reading rule below bar:** the honest end-to-end number is the **product**
    `end_to_end_completeness = retrieval_recall × conditioned_citation_recall` (telescopes to
    `|cited ∩ relevant| / |relevant|`, completeness over ALL relevant, since cited ⊆ arrived). At the current
    fused recall ~0.38 the answer can miss ~60% of relevant evidence while the conditioned value looks
    excellent. **If synthesis is run while retrieval is below bar, report the product, never the conditioned
    value alone** — it is a product *diagnostic*, not a graded citation-recall.
  - **Consequence:** the conjunction's left operand is not met, so the graded run **cannot** be framed as
    "retrieval validated, now test synthesis." Retrieval reaches its grounded bar first.
  - **RESOLUTION — the grounded recall bar = `E / 0.75`**, where `E` = the end-to-end evidence-use
    completeness target (what fraction of ALL relevant clauses a good answer must draw on) and `0.75` is the
    citation-recall gate. This derives the recall bar from the eval's actual success criterion, not a guess —
    what was missing when 0.70 was ungrounded. **nDCG stays DIAGNOSTIC, not the gate** (it measures top-10
    ranking; the end-to-end outcome is bounded by recall, not nDCG). Not "achievable recall" either (that is
    fit-to-result). **Coherence requirement:** the recall-gate `k` MUST equal the synthesis evidence-feed cap
    (top-`k` fused → synthesis), so `recall@k` = the fraction of relevant that arrives = the product's
    retrieval term (today `DEFAULT_UNION_CAP=20` ≠ 50 — align before the graded run).
  - **`E = 0.5`, `k = 50` SET (grounded bar = 0.5/0.75 = `recall@50 ≥ 0.667`).** Rationale: a complete answer
    draws on the *majority* of the relevant evidence (principled, not fit — current 0.38 fails it, so it is a
    real target, not a lowered bar). Framing endorsed 2026-07-18; `E` adjustable but do NOT lower it to make
    0.38 pass (0.38 fails E=0.375/0.45/0.50 alike). `k=50` chosen so the synthesis evidence-feed cap must be
    aligned to 50 (today `DEFAULT_UNION_CAP=20` — align before the graded run so `recall@50` = the arrived set).
    **Achievability flag:** raw-hybrid `recall@200 = 0.65` is the retrieval *ceiling*, and `recall@k ≤
    recall@200`, so 0.667 is *above* today's ceiling — reaching it requires improving the retriever itself
    (legal-tuning / stronger model / higher-recall retrieval), NOT just rerank. The gap (0.38 → 0.667, ceiling
    0.65) is the retrieval investment the grounded bar demands; 0.38 is the gap to close, not a bar to lower to.
    The three earlier gate options collapse: nDCG-gate dropped (wrong metric), recall regrounded as `E/0.75`
    (grounded, not fit), retrieval-investment is the *consequence* of the grounded bar.

**STRATEGY — ceiling, not bug (2026-07-18): decouple integration from retrieval quality.** Both real bugs are
fixed (SQL-newline dead-lettering; NOTE the dense leg was never misconfigured for ACORD — summary=text — so
0.45–0.47 pool-nDCG is BGE-M3's genuine off-the-shelf ceiling, not a post-bug residual). The remaining gap to
ACORD's 0.54–0.64 baseline is a **capability ceiling**, which gets invest-or-scope, not more diagnosing.
- **The query-graph INTEGRATION PROOF and the graded eval are separate achievements; retrieval quality must
  not block the integration.** A correct graph over a known-weak retrieval leg is still a correct graph (a
  known-weak leg, not a broken architecture) — the KI-1 move: a bounded, liftable, documented limitation,
  proceed on the correct path around it. RAG's integration-readiness is DONE: every query-side capability is
  built, tested, registered, and ARD-published (hybrid_search, reranking, fusion, chunk_read, rlm_synthesis),
  so GraphWright can compile+run the query graph binding them (that graph is compiler work, not this repo's).
- **Run the graded eval as an HONEST BASELINE, not a pass/fail to force.** Report the real conjunction:
  retrieval below its grounded bar → the eval does not pass; and the **product** `retrieval_recall ×
  conditioned_citation_recall` = true end-to-end evidence-use (caps low at ~0.38 regardless of synthesis).
  "Retrieval is the bottleneck at 0.38, here is the measured state" is a valid, useful result. Do NOT lower E.
- **Retrieval quality becomes its own scoped workstream (T41), NOT inline W10 work.** Its first question is
  ceiling-vs-tuning-gap (below), and its broader question is whether clause-level legal retrieval wants a
  different approach than the summary-era design — the THIRD time the summary-centric assumption has bitten
  (synthesis needed text T40; general dense-over-summary for real docs; and this clause-level retrieval gap,
  all downstream of the same clause-level-precision demand).

**Description (build scope):** Ingest ACORD (Atticus Clause Retrieval Dataset: CC-BY-4.0, BEIR, 114
attorney-authored queries, ~126k graded query-clause pairs, corpus of SEC/EDGAR + F500 ToS clauses) into
the store via the pipeline and run its expert queries through hybrid search + rerank + fusion, scored
against qrels for the recall gate. Rehydration to text is the **`chunk_read` governed capability (T38)**,
not caller-side plumbing. Does NOT exercise the RLM chunker (pre-segmented clauses), so the RLM
keep-vs-optional call stays at GATE-2b. **LLM-generated queries are rejected as circular; if ACORD is
unusable, return to the human before any alternative.**
**Dep:** T21, T22, T38 (chunk_read), T40 (sidecar). License + corpus provenance verified (2026-07-12).

**Ingest constraint (load-bearing, verified before build 2026-07-17):** ACORD ingest MUST go through
`ChunkWriter.write_document` (index upsert + sidecar write under one gate + completeness guard), **never a
direct `store.upsert_chunk`** — a direct write would populate the index while bypassing the sidecar and
break the invariant below. Pre-segmented ACORD clauses skip the RLM chunker but still flow chunk → embed
(BGE-M3) → `write_document(records, texts=...)`.

**Rehydration invariant + eval-integrity handling (pinned before the run):** the chain
**indexed ⟹ sidecar-text-present** (T40 guard) and **fusion surfaces only indexed chunk_ids** (hybrid_search
returns only `Chunk`-index rows; reranking passes ids through; the graph leg is empty on the retrieval
path) → `chunk_read` **cannot miss on a clean ingest**. Therefore a `chunk_read` `KeyError` during the eval
is a **can't-happen**, i.e. the invariant was violated (a real ingest/sidecar bug). So the harness treats a
`chunk_read` raise as an **eval-integrity failure**: caught, surfaced loudly with the query and chunk_id,
**halt-and-investigate** — never folded into a metric, never swallowed by a generic run abort. (Sits
alongside the already-pinned zero-arrival-query handling: those count as retrieval failures under the
recall gate and are excluded from the citation-recall population.)

**First synthesis run on pipeline-persisted text:** T40 (persist) + T38 (rehydrate) close the loop, so this
is the first time synthesis runs on pipeline text rather than test-provided text (earlier proofs used the
phantom-manifest text source). Carry into the run notes.

**ACORD acquired + fully mapped (2026-07-17).** Source pinned: HuggingFace `theatticusproject/acord`
(CC-BY-4.0), `scripts/acquire_acord.py` (download + extract + attribution under gitignored `data/acord/`).
BEIR format: `corpus.jsonl` = **3,931 clauses** `{_id, text}`; `queries.jsonl` = 114 `{_id, text, metadata}`;
`qrels/{test,valid,train}.tsv` = `query-id, corpus-id, score`. **Test split = 57 queries**, 61,988 judged
pairs. Grade scale **0–4** (readme 1–5 minus 1; "zero-score = irrelevant").

**Locked build decisions (grounded from ACORD's own semantics, set before results):**
- **Relevance floor = qrels grade ≥ 2** (readme 3★ "partially relevant" and up) — ACORD's OWN official bar
  (their 3-star precision@5). Three converging grounds: official metrics use a 3★ floor; grade 1 is
  explicitly "non-relevant but helpful"; and grade ≥ 2 gives ~10.9 relevant/query matching the readme's
  stated ~10 (grade ≥ 1 balloons to ~31/query, contradicting the benchmark). NOT a judgment call, not
  overridable — anything else measures against a relevance set ACORD's authors don't endorse and is
  incomparable to ACORD's results. grade ≥ 3 is stricter than the floor; grade ≥ 1 counts non-relevants.
- **Eval split = standard held-out test (57 queries).** The graded run is on test; train/valid are for
  tuning, not grading (never score an easier subset).
- **Metric roles (unambiguous):** **recall@50 binary at grade ≥ 2 is the pass/fail GATE** (recall@50 ≥ 0.70
  = the retrieve→rerank→fusion legs surface ≥70% of the ~10.9 relevant clauses/query in the top 50 —
  coherent with what was calibrated). **nDCG@10 uses the full graded 0–4 scale** (ACORD-official, NO
  threshold — nDCG rewards higher grades by design), **reported-not-gated**, the ranking diagnostic.
  recall@10 also reported. The relevance floor applies to the binary recall gate only; nDCG deliberately
  does not threshold.
- **Ingest mapping:** one clause = one chunk; `chunk_id` minted with `source_doc_id` = the ACORD `_id`, so a
  retrieved `chunk_id` rsplits back to its corpus-id for scoring with **no side map** (cannot drift);
  through the real `ChunkWriter.write_document` (index + sidecar, same-hash gate) — a real end-to-end
  ingest, not synthetic.
- **Dry-run before the graded run (pinned discipline):** run the retrieval legs against the grade-≥2 test
  qrels FIRST to confirm recall@50 ≥ 0.70 is achievable, not aspirational. If off, recalibrate BEFORE the
  graded run (never after), reasoning recorded. **Report the dry-run recall at the gate before the graded
  measurement locks.**

**Ingest completed (2026-07-17):** 3,931/3,931 clauses in `ragwright_acord`, 0 dead-letters — after fixing a
store bug the real corpus exposed: `_sql_str` didn't escape newlines, so ArcadeDB's SQL tokenizer rejected
every multi-line clause on upsert and silently dead-lettered 685 (~17%), exactly the long content-rich ones
(commit `51e8492`, regression test added). Two macOS operational fixes: BGE-M3 and the reranker default to a
multi-process pool that DEADLOCKS on macOS → forced single-process CPU (`devices=["cpu"]`) in the ingest and
the runner; embedding `max_length=1024` caps the vector compute (full text preserved in the sidecar).

**DRY-RUN RESULT — BELOW THRESHOLD (2026-07-17):** on the complete 3,931-clause index, 57 test queries:
`recall@50 = 0.379` (gate ≥ 0.70), `recall@10 = 0.137`, `nDCG@10 = 0.138`. Below-baseline nDCG was the
ADR-0011 red flag → diagnosed fully before any recalibrate-vs-fix verdict.

**DIAGNOSIS COMPLETE (2026-07-18) — retrieval is HEALTHY; the low full-corpus numbers are a TASK MISMATCH,
not a bug:**
1. **Leg isolation** (full-corpus, pool=200): dense recall@50 0.301 / nDCG@10 0.109; sparse 0.232 / 0.089;
   fused 0.283 / 0.102. Both legs work (dense > sparse), and **RRF fusion slightly HURTS recall@50**
   (0.283 < dense 0.301) — a real, tunable finding (the weak sparse leg drags dense-found relevants below
   rank 50). Not a dead/broken leg.
2. **Spot-check** (top-10 fused, length-annotated): hits are **topically plausible, not garbage** — "Audit
   Rights" → all 10 audit clauses; "England Governing Law" → 8/10 relevant, first at rank 1; "multiple
   governing laws" → all governing-law clauses but *single*-law ones (the "multiple" nuance missed, first
   relevant at rank 16). Failure class = fine-grained legal-nuance ranking = genuine difficulty, NOT a bug
   (relevant clauses mean 583 chars — no short-vs-long embedding pathology seen).
3. **Baseline reference (ADR-0011 completed)** — ACORD paper Table 3 retrieval-only nDCG@10: BM25 0.540,
   MiniLM bi-encoder 0.572, OpenAI-large 0.641 (topline MiniLM+GPT4o-reranker 0.812). Those are per-query
   judged-POOL rankings (~1,088 explicitly-annotated clauses/query), not full-corpus. Ranking my retriever
   **like-for-like on the judged pool: nDCG@5 = 0.417, nDCG@10 = 0.452** — squarely in ACORD's baseline band
   (slightly below BM25's 0.54, as expected for off-the-shelf BGE-M3). So the earlier full-corpus nDCG 0.10
   was the *harder task* (rank 3,931 vs rank ~1,088), not a defect.

**VERDICT:** NOT a bug — retrieval performs at ACORD-baseline level on the like-for-like task. The `recall@50
≥ 0.70` bar was an ungrounded pre-run guess against the WRONG task: full-corpus recall@50 on ACORD is far
harder than the pool-ranking ACORD baselines measure, and ACORD publishes no recall@50 to anchor it. Two
real levers remain (improvements, not bug-fixes): (a) **fusion tuning** — dense-alone beats fused @50, so
RRF weighting / dropping the weak sparse leg is worth testing; (b) lift embedding `max_length`. **OPEN
DECISION at the gate (do NOT lock a graded bar until decided):** recalibrate the bar to grounded/achievable,
and/or switch the retrieval metric to pool-ranking nDCG@10 (directly comparable to ACORD baselines), and/or
invest the fusion/max_length improvements first.

### Task T40: Chunk-text sidecar — persist full chunk text at ingest (FR-I.3, RAC to follow)

**Why:** grounding for T38 (`chunk_read`) surfaced that the full chunk **text is persisted nowhere**. The
index is dense-over-summary by design (`ChunkRecord` = summary + vectors, no raw text), and the text is
used once to compute the `chunk_id` content hash then **discarded** (`chunk_write.to_chunk_record`). Both
the `ChunkRecord` docstring and the `SynthesisChunk` docstring claimed the text *"lives in the parse
manifest keyed by chunk_id"* — **a phantom manifest that was never written** (the only parse manifest is
the whole `DoclingDocument`, keyed by doc, not per-chunk). So the synthesis leg (FR-Q.5) was built against
a text source that never existed; its earlier proofs (T28) ran on test-provided text. **T40 provides the
text source synthesis has always assumed and never had** — the first pipeline-persisted-text path.

**Confirmed synthesis needs text, not summary:** (1) architectural — `SynthesisChunk` is `{chunk_id, text}`
and the extractor operates over `.text`, never reading `summary`; (2) eval — ACORD qrels are relevant
*clause text* and citation-recall grades citing the actual clauses; summaries are lossy for legal language,
so summary-fed synthesis would grade a mismatched measure. Summary does **not** suffice.

**Design (chosen: sidecar, Option 1 of 3):** `ChunkTextStore` (`store/chunk_text.py`) — one JSON file per
`source_doc_id`, mapping the canonical `chunk_id` string → full text. NOT part of the swappable `Store`
seam (holds no vectors, answers no query). Rejected: adding `text` to the ArcadeDB schema (reverses the
dense-over-summary decision, bloats the index with large legal text) and reconstruct-from-parse-manifest
(recompute-at-retrieval the architecture avoids). Shape trimmed to `{chunk_id, text, source_doc_id}` for
T38 (dropped `summary` — no consumer reads it).

**Same-gate coupling (load-bearing):** the sidecar write is inside `write_document`, at the same loop point
as `upsert_chunk`, driven by the **one** content-hash gate — an unchanged doc skips both, a changed chunk
writes both, so index and sidecar cannot drift. A completeness guard rejects any `records` write whose
`texts` map is missing a chunk_id **before any write**, so a chunk can never land in the index without its
text in the sidecar. Delete-by-`source_doc_id` (`delete_document`) is built as the T34 lifecycle seam.

**Integrity check (self-verifying store):** `ChunkTextStore.put` asserts `sha256(text) == chunk_id.content_hash`
before persisting — the `chunk_id` already carries the hash `ChunkId.of` computed over these exact bytes at
chunking, so the sidecar can prove it stores the faithful text for the id. A mismatch (loop index error,
mismatched map) is the **silent-wrong-text** class that would otherwise surface only as synthesis citing a
structurally-valid-but-wrong `chunk_id` — which citation-recall may not catch. Write-time-cheap (the hash
was already computed at ingest; the check compares a value in hand against a value in hand) and impossible
to add faithfully later; rejected at the store boundary so no caller can bypass it.

**Files:** `src/rag_wright/store/chunk_text.py` (new), `src/rag_wright/capabilities/chunk_write.py`
(`ChunkWriter` gains required `text_store`; `write_document` gains `texts` + gated sidecar write),
`tests/store/test_chunk_text.py` (new), `tests/capabilities/test_chunk_write.py` (updated + same-gate and
guard tests). Contract comments corrected in `contracts/chunk.py` and `rlm_synthesis.SynthesisChunk`.
**Verify:** `uv run pytest tests/store/test_chunk_text.py tests/capabilities/test_chunk_write.py` (15 passed,
1 skipped live). Full suite 395 passed + 26 skipped. **Status:** awaiting-approval. **Dep:** T17, T19, T20.

### Task T38: chunk_read — governed text-rehydration capability (FR-Q)

**Why:** fusion (FR-Q.4) yields a capped set of `chunk_id`s; synthesis (FR-Q.5) extracts over full chunk
text the index does not hold (dense-over-summary). `chunk_read` is the rehydration step between them,
reading the T40 sidecar → text per `chunk_id`. Exposed as a **governed, discovered, bound capability**
(the reconciliation's requirement: rehydrate is a node, not caller-side plumbing), so GraphWright's
compiler binds a real registered capability rather than doing the lookup itself.

**Design:** mirrors the `fusion` FR-Q node (function-kind, result contract, `register_*`). `chunk_read.py`:
`ChunkText` = `{chunk_id, text, source_doc_id}` (summary dropped — no consumer reads it; `source_doc_id`
derived from the id, no second store read), `ChunkReadResult{chunks}`, `chunk_read(chunk_ids, *, text_store)`,
`register_chunk_read`. **No silent drop:** a `chunk_id` the sidecar cannot supply (orphan, or an id never
received) raises `KeyError`, never skipped — the FR-Q.6 no-claim-without-citation discipline applied to
evidence. Registered as a canonical FR-Q slug (precedent: `fusion` is FR-Q.4, not an FR-C-catalog capability
either); the parametrized manifest test now authors + publishes chunk_read's ARD manifest.

**Note (carried from T40):** this is the read side of the first **pipeline-persisted-text** path — synthesis
had only ever run on test-provided text (phantom manifest). T38 + T40 close that loop end to end.

**Files:** `src/rag_wright/capabilities/chunk_read.py` (new), `capabilities/registry.py` (+`chunk_read`
canonical slug), `capabilities/manifests.py` (+ARD manifest spec), `tests/capabilities/test_chunk_read.py`
(new). **Verify:** `uv run pytest tests/capabilities/test_chunk_read.py` (4 passed) + manifest/registry
regression (55 passed). Full suite 400 passed + 26 skipped. Publish to the shared ARD root
(`scripts/publish_manifests.py`) on approval. **Status:** awaiting-approval. **Dep:** T40.

**GRAPH-LEG CORRECTION (2026-07-18) — supersedes the "ceiling" framing; pauses the premature handoff.**
The 0.38 dry-run is **dense+sparse ONLY**: the graph leg was empty (`_EMPTY_GRAPH`) and no ACORD graph was
ever built (`entities: 0`). Crucially, the graph is **NOT bounded to entity/`CONTRACTS_WITH`** — that is
merely the CUAD/EDGAR instantiation. The graph capability is a **general, domain-adaptable relationship
layer**, and constructing domain-appropriate relationships (a **clause-relation graph** for ACORD) plus a
**clause-anchored graph retrieval**, fused into hybrid search, is the *design intent* of putting graph in
the hybrid engine — not a new capability. (My earlier "graph leg inapplicable to ACORD / three mismatches"
read was WRONG: it assumed the one fixed `CONTRACTS_WITH` schema.) So **0.38 is a TWO-LEG PARTIAL
measurement, and the "capability ceiling" verdict was premature.** Completing the three-leg measurement
precedes any ceiling/T41 judgment; the graded-run handoff pauses until retrieval is fully measured.
**Design the graph EMPIRICALLY, not by guess:** analyze ACORD's relevance structure (for failing queries,
what relates a query's relevant clauses to each other / connects missed relevants to retrievable anchors —
shared entities, clause category, cross-references, semantic clusters) → construct the edges the data
implies → wire clause-anchored graph-expansion into the candidate set → re-measure three-leg recall@50 /
pool-nDCG. If it moves toward the grounded bar, the "ceiling" was a missing leg; if not, the ceiling is real
across the full design and T41 stands with much stronger justification.

**HANDOFF to GraphWright — UN-PAUSED for the INTEGRATION PROOF (2026-07-18, integration-first).** Strategic
call: integration proof first, retrieval optimization later. The three-leg question is resolved (the graph
leg does not help ACORD — mechanism proof below), so the retrieval baseline is settled at two-leg ~0.38, and
that is **good enough to produce cited answers end to end** — which is all the integration proof needs.
**The graded number is a BASELINE the recomposition experiments will move, NOT a pass/fail gate.** The five
query-graph capabilities are PUBLISHED + DISCOVERABLE in the shared root `~/.air/registry` (verified
2026-07-18: `hybrid_search`, `reranking`, `fusion`, `chunk_read`, `rlm_synthesis` — valid URN + kind +
representativeQueries each; none local-only) → GraphWright's discover-and-compile is unblocked. Ready:

**INTEGRATION PROOF PASSED (GraphWright dry-run, 2026-07-18).** Compiled-from-discovery (5 nodes, all bound
by registry discovery) → real retrieval over the 3,931 ingested ACORD clauses → grounded, query-relevant,
chunk-tied citations, end to end. "Audit Rights": 10 citations, 6/6 checked resolve via `chunk_read`, 6/6
extracts match their cited chunk, extracts topically correct. RAG's capabilities are integration-validated.
The one low number is RETRIEVAL not synthesis (1/12 cited-are-qrel-relevant = the known recall@50 0.379 /
query-representation gap, T41), and it's a top-10-capped dry-run sanity signal, not the citation-recall
metric. Two RAG live-bugs surfaced only by real data along the way (SQL-newline dead-letter; `[` in extract
text) → **standing test-discipline item: RLM/synthesis/store live tests want at least one deliberately
messy fixture (brackets, newlines, quotes), not just clean values** — fold into the latent-hardening pass.
- **Capabilities** — all query-side ones built, tested, registered, and ARD-published to `~/.air/registry`
  (`hybrid_search`, `reranking`, `fusion`, `chunk_read`, `rlm_synthesis`, `graph_query`, `generation`).
- **Store** — `ragwright_acord` DB (3,931 chunks) + the chunk-text sidecar (`data/acord/chunk_text/`) for
  `chunk_read`. **Eval assets** — `eval/acord.py` (loader, grade≥2 floor, test split), `eval/acord_retrieval.py`
  (recall + graded nDCG), the diagnostics under `scripts/`.
- **Feed-cap / gate-`k` coherence — CORRECTION to my earlier proposal:** do NOT change `DEFAULT_UNION_CAP`
  (20 is a spec value, §16.7). Instead the graded run binds `fuse(..., cap=k)` via the existing parameter so
  `recall@k` = the arrived set (the product's retrieval term). **Open sub-choice:** `k=20` (the spec
  production cap — most production-representative; `recall@20` is lower) vs `k=50` (PIN 3 as-set — more
  synthesis context but above the spec production cap). Recommend the graded run set `cap` explicitly = the
  chosen gate-`k`; flagged for GraphWright/user, not silently defaulted.
- **Read the result as the product** `retrieval_recall × conditioned_citation_recall` (PIN 3); retrieval is
  below its grounded bar, so the run is an honest baseline, not a forced pass. Do NOT lower E.

### Task T41: Retrieval-quality mini-project — ceiling-vs-tuning-gap, scoped workstream (T33 finding)

**Why:** after fixing the one real ingest bug (SQL-newline), ACORD retrieval sits at full-corpus recall@50
0.379 / pool-ranking nDCG@10 0.45–0.47 vs ACORD's published retrieval-only baselines (nDCG@10: BM25 0.540,
MiniLM 0.572, OpenAI-L 0.641). Dense was never misconfigured (summary=text for pre-segmented clauses), so
this is a **capability ceiling, not a bug** — it gets invest-or-scope, in its OWN workstream, NOT inline in
the integration milestone (which proceeds on current retrieval; the weak leg is documented, KI-1 style).

**Scope — answer the ceiling-vs-tuning question BEFORE any model swap:**
1. **First task (the fork):** establish *what retriever produced ACORD's 0.54–0.64 baseline*. If it used a
   stronger/differently-tuned retriever, our gap is "weaker setup than baseline" → match it (cheaper). If it
   used BGE-M3 or comparable and still beat 0.45–0.47, then BGE-M3 has headroom we're not reaching (chunk
   granularity, query formulation, fusion weighting, un-capped `max_length`) → tune to its actual ceiling,
   not swap. Determines expensive-model-change vs cheaper-tuning-close.
2. **Cheap levers to test regardless:** drop/downweight the weak sparse leg (dense-only already beats fused:
   pool nDCG 0.452→0.469, recall@50 0.291→0.307); un-cap embedding `max_length` (1024→full; marginal, ~1–3%
   of clauses); higher hybrid pool feeding rerank.
3. **Broader question to ASK (not answer now):** does clause-level legal retrieval want a different retrieval
   approach than the summary-era design? The summary-centric assumption has now bitten three times (T40
   synthesis; general dense-over-summary for real docs; this retrieval-quality gap) — all downstream of the
   clause-level-precision demand. Scope the project to ask it, not default to a model swap and hope.

**RELEVANCE-STRUCTURE ANALYSIS (2026-07-18, `scripts/diagnose_acord_relstructure.py`) — STRONG positive
signal for the graph leg; confirms 0.38 was a two-leg partial.** 57 queries: relevant-set cluster tightness
**0.686** >> random-pair 0.561, and > query→relevant **0.534** (gap **+0.152**) — the relevant clauses
cluster tighter among themselves than the query is to them (the query sits OUTSIDE the relevant cluster),
the exact case a clause-anchored graph beats query-anchored retrieval. **50/57 queries have a retrieved
anchor** to expand from (7 zero-anchor → need better base retrieval, not a graph). Missed relevants are
reachable from retrieved anchors: **missed→anchor max cosine mean 0.706 (88% ≥0.6, 56% ≥0.7)**. So a
clause-relation graph — clause-clause semantic kNN and/or a **category graph** (ACORD's query `category`
metadata + `graph_extraction`'s clause-category tagging already exist) — with **clause-anchored expansion
from retrieved anchors** should recover much of the ~70% dense/sparse miss → real headroom toward the 0.667
grounded bar. **T41 build:** construct the graph the data implies, wire clause-anchored expansion into the
hybrid candidate pool, re-measure three-leg recall@50 / pool-nDCG. Residual the graph cannot fix: 7
zero-anchor queries + ~12% of missed relevants unreachable (<0.6 to any anchor).

**T41 MECHANISM PROOF — the semantic-kNN graph leg does NOT deliver recall (2026-07-18)**
(`scripts/diagnose_acord_knn_expansion.py` RRF; `scripts/diagnose_acord_knn_rerank.py` rerank).
- **RRF** fusion of clause-anchored expansion HURTS recall@50 (0.301 → 0.23–0.28 across A∈{5..50},
  E∈{5..20}) — expanding from mostly-irrelevant anchors floods the pool with noise RRF can't downweight.
- **+ cross-encoder rerank** (the real precision gate): **NEUTRAL** — recall@50 0.301 → 0.303 (+0.002),
  pool-nDCG@10 +0.03. The reranker filters the noise but can't PROMOTE the expanded relevants (query-distant).
- **ROOT CAUSE — the query–clause representation gap.** query→relevant cosine **0.534 is BELOW** the
  random-relevant-pair baseline **0.561**: the query embeds FARTHER from its own relevant clauses than
  random. Clause-clause structure is strong (cluster 0.686, missed→anchor 0.706), so expansion REACHES the
  relevants, but ranking them for the query needs a query-relevance signal that is fundamentally weak for
  ACORD's short type-phrase queries; neither RRF nor the cross-encoder overcomes it.
- **The lever that DOES move recall:** a bigger BASE pool + rerank (BASE 50→100 gave two-leg recall@50
  0.301→0.379). The reranker recovers recall from the base pool; graph-expanded query-distant candidates it
  cannot promote.
- **IMPLICATION:** headroom is query-side (query representation; bigger base pool + rerank) and — per ACORD's
  own Table 3 — an LLM reranker (GPT4o reached nDCG@10 0.81 vs 0.54–0.64 for bi/cross-encoders), NOT a
  semantic-kNN graph leg. The **category graph** (direct label-retrieval, a DIFFERENT mechanism that bypasses
  the embedding gap but is coarse + needs clause category-tagging via `graph_extraction`) is the remaining
  system-proof test — tempered expectations, and the graph leg's role for ACORD now looks small.

**RETRIEVAL REFERENCE BASELINE (integration-first, concluded 2026-07-18).** The retrieval investigation is
concluded for now, and recorded as a **baseline-with-diagnosis, not a shortfall**: two-leg BGE-M3 hybrid +
cross-encoder reaches **recall@50 ~0.38 / pool-nDCG 0.45–0.47** (near BM25/MiniLM — baseline-healthy
like-for-like); **root cause = the query-representation gap** (short type-phrase queries embed farther from
their relevant clauses than random pairs); the **graph leg does not help** (reachability ≠ rankability under
the weak query signal); the **indicated lever per ACORD Table 3 is an LLM reranker** (GPT4o nDCG@10 0.81 vs
0.54–0.64 bi/cross-encoders). This is the measured starting point the later recomposition experiments move.

**COMPOSITION-EXPERIMENT BACKLOG (post-integration; the compose→recompile→evaluate menu).** When the
integration proof lands, these become the experiments the system was built to run — each an add/remove-a-
capability, recompile, new-number loop against the ACORD baseline above:
- **LLM reranker** — ACORD's indicated lever (Table 3): swap the cross-encoder for an LLM reranker, recompile,
  measure the recall/nDCG lift toward the ~0.8 the paper demonstrates.
- **Category label-retrieval** — the graph mechanism that may sidestep the query-representation gap by
  matching on clause-category *label* (not embedding); needs clause category-tagging via `graph_extraction`.
- **Base-pool sizing** — bigger hybrid pool feeding rerank (BASE 50→100 already showed recall@50 0.301→0.379);
  quantify the curve. Cheap, optional, NOT gating anything under integration-first.

**Status:** investigation concluded → parked as the composition-experiment backlog (own workstream, post-
integration). **Dep:** T33 (baseline measured). base-pool+rerank is **optional, no longer a gating task**.

### Task T42: RLM/synthesis latent-hardening pass (parked, post-integration)

**Why:** two "works-by-luck / caught-only-on-real-data" items surfaced during the integration proof
(2026-07-18). Both confirmed non-blocking (integration passed), so parked — but real hardening, promoted to a
visible task rather than left as prose notes:
1. **Worker query/slice threading is unenforced** (see the LATENT HARDENING ITEM note at the top of this
   file). The RLM leaf dispatch `task({description: "handle leaf depth D", subagentType: "rlm_slice_worker"})`
   threads NEITHER the query NOR the slice; query-relevant extraction works only because the orchestrator
   model *chooses* to thread them (GraphWright dry-run verdict: did not bite — but it's model-luck, not
   enforcement). Affects standalone synthesis too.
2. **Live tests use clean fixtures**, so two real-text bugs stayed invisible until real data hit them:
   the SQL-newline dead-letter (T33 store fix) and `[` in extract text (the `_parse_slice_outputs` fix).

**Scope (design question to resolve first, then implement):**
- **Enforce worker query + slice threading.** The RLM workflow is GENERAL (chunking has no query; synthesis
  does), so the query cannot be hardcoded into `RLM_WORKFLOW_JS`. Decide between / combine: **(a)** the
  synthesis capability bakes the query into the worker's system prompt (`worker_system_prompt`) — enforced,
  capability-specific, does NOT touch the general workflow or the drift test; **(b)** the workflow threads
  the slice `items` into the leaf-dispatch description so the worker sees its slice (consistent with "the
  *orchestrator* never sees the whole set"; the worker MUST see its own slice) — this DOES touch
  `RLM_WORKFLOW_JS` + `SKILL.md` (byte-identical, drift-tested, re-verify after). Prefer (a) for the query
  (cleanest, no general-workflow change); resolve whether (b) is needed for the slice or the PTC path already
  covers it.
- **Non-stub worker test.** Current synthesis tests stub the sub-agent responders (they return canned output
  regardless of dispatch content), so they never assert the worker *received* the query/slice. Add a test
  that drives the real worker and asserts the query + slice reach it.
- **Messy-fixture live-test discipline.** Add at least one deliberately messy fixture (brackets, newlines,
  quotes) to the RLM/synthesis/store live tests so real-text bugs are caught by the suite, not by production.

**Acceptance:** the query (and slice) reach the worker by enforcement, verifiable in a non-stub test; a messy
fixture exists in the relevant live tests. **Verify:** `uv run pytest tests/capabilities/test_rlm_synthesis.py
tests/capabilities/test_rlm_method.py` (+ the SKILL.md↔RLM_WORKFLOW_JS drift test if option (b) is taken).
**Files:** `capabilities/rlm_synthesis.py` (worker prompt / `_request`); possibly `skills/rlm/agent.py` +
`skills/rlm/SKILL.md` (if (b)); `tests/capabilities/test_rlm_synthesis.py`, `test_rlm_method.py`.
**Status:** DONE (2026-07-18). Implemented: **(1)** slice threaded into every leaf dispatch
(`JSON.stringify(items)`) in `RLM_WORKFLOW_JS` AND `SKILL.md`, byte-synced (drift test green) — the
committed pre-T42 state had NEITHER file threading the slice (both `"handle leaf depth D"`; GraphWright had
misread `agent.py` as already serializing items). **(2)** query enforced by baking it into the synthesis
worker's system prompt (`_build_agent(chunks, query)`) — NOT the dispatch description: the query isn't in the
workflow's JS scope, so description-threading would reintroduce model initiative / break the general
workflow's capability-agnosticism; system-prompt baking is the enforceable contract. **(3)** two non-stub
tests: `…messy_slice_by_enforcement` (query in prompt + a bracketed/newlined/quoted slice reaches the worker
— fixture discipline) and `…each_worker_only_its_own_slice` (forces a 4-worker split, asserts each worker
got EXACTLY its own partitioned slice + the query — the multi-worker path the flat dry-run never exercised,
GraphWright point 3). Full suite 412 passed + 27 skipped; ruff clean.
**Dep:** T28 (synthesis), T38 (chunk_read). GraphWright's node should re-pull the hardened skill (strictly
more robust; same output, now enforced).

### Task T43: Emit `capabilityInterface` typed I/O on authored manifests (GraphWright ADR-0030) — BLOCKED

**Why:** GraphWright's lowering pass verifies that a realization's capabilities actually chain (inputs/outputs
produce what a step needs). Today our manifests carry a rich `description` + representative queries but **no
typed I/O interface**, so their checker can only verify a *model's asserted* interface — a wrong interface
still "verifies" (they caught exactly this: a capability force-fit to a step it cannot do). They asked us to
confirm/correct a guessed typed interface (`capabilityInterface`) for the 5 query-graph capabilities. We
grounded all 5 against the real bound callables and are **owning the emission** (governed data we author),
rather than letting GraphWright hand-write into our registry files.

**Grounded confirm/correct (the interfaces to emit; full detail + reply in
`docs/handoff/2026-07-20_graphwright_capability_interface_reply.md`):**
- `hybrid_search` in `query: text` → out `candidates: {chunk_id, source_doc_id}[]` (filters/k are config).
- `reranking` in `query: text` + **`passages: {chunk_id, source_doc_id, text}[]`** (needs TEXT, not ids) → out
  `ranked: {chunk_id, source_doc_id, score}[]` (top_k config). **The load-bearing correction.**
- `fusion` in `reranked: rerank_result` + `graph: graph_answer` (two distinguishable legs, not variadic) → out
  `fused: {chunk_id, sources[]}[]` (union, no score; cap config).
- `chunk_read` in `chunk_ids: chunk_id[]` → out `chunks: {chunk_id, text, source_doc_id}[]` (ordered, drops nothing).
- `rlm_synthesis` in `query: text` + `chunks: {chunk_id, text}[]` (takes text, doesn't rehydrate) → out
  `answer: text` + `chunk_ids: chunk_id[]` (citations) + `slice_outputs: {chunk_id, extract}[]`.
- Set completeness: **`graph_query`** (produces fusion's 2nd input) and **`generation`** (alt answer step)
  also want interfaces if the whole graph is to be governed.

**BLOCKED ON GRAPHWRIGHT (two asks, in the reply) — do NOT build until resolved + approved:**
1. **Schema lockstep.** Both `ard.py` (`RegistryEntry`, `extra="forbid"`, `ard.py:55`) and GraphWright's
   `entry.py` (mirror, ADR-0005) forbid extra fields. A manifest carrying `capabilityInterface` fails to load
   on whichever side hasn't added the field. Neither side ships until both schemas accept it — agree the
   top-level field name + nested shape verbatim.
2. **Type vocabulary.** GraphWright's `{text, chunk_id}` scalar set can't express the compound records that
   actually flow (`{chunk_id, source_doc_id}`, `{…, score}`, `{chunk_id, text, source_doc_id}`,
   `{chunk_id, sources[]}`) — and that granularity is what lets their checker catch the `reranking`-needs-text
   force-fit. Agree scalar-vs-compound and the shape so we emit something their checker can read.

**Scope (when unblocked):** add an optional `CapabilityInterface` model to `ard.py`
(`RegistryEntry.capability_interface`, camelCase `capabilityInterface`, matching the agreed wire shape); thread
it through `ManifestSkeleton.author(...)`; declare the confirmed interfaces at the `register_*` calls for the 5
(+ `graph_query`, optionally `generation`); re-emit the manifests. Short RAG-side ADR mirroring GraphWright
ADR-0030 (the cross-repo coordination record, ADR-0005 style).
**Acceptance:** a manifest carrying `capabilityInterface` round-trips through `RegistryEntry` (conformance
test); `write_manifest` emits it; the emitted interfaces match the real signatures above.
**Verify:** `uv run pytest tests/capabilities/` (ard conformance + registry). **Files:**
`src/rag_wright/capabilities/ard.py`, `registry.py`, the per-capability `register_*` calls,
`tests/capabilities/…`, a new `docs/adr/` entry, `tasks.md`.
**Status:** DONE (2026-07-20, ADR-0021). GraphWright answered both asks (nominal typing, not compound types;
their `entry.py` mirror is in). Implemented: `CapabilityInterface` (plain BaseModel, `extra="forbid"`,
snake_case inner keys) + `NOMINAL_TYPE_VOCABULARY` (mirror of their §3 table, validated at author time) in
`ard.py`; optional `capability_interface` on `RegistryEntry` (→ `capabilityInterface` on the wire); threaded
through `ManifestSkeleton.author()`; declared the 7 interfaces in `manifests.py` (5 + graph_query + generation),
grounded in the real callables. 13 new tests (interfaces match signatures; reranking needs `chunk_with_text`;
vocabulary rejects typos/list-sugar; snake_case inner keys on the wire; round-trips through `extra="forbid"`).
Full suite **426 passed + 27 skipped**, ruff clean. Confirmation to GraphWright drafted at
`docs/handoff/2026-07-20b_graphwright_interface_confirmation.md` (confirms §4, answers abstain = boolean flag /
synthesis citation shape, flags 2 chain-level notes). **Contract now CLOSED (2026-07-20):** GraphWright resolved
the one open item — fusion output retypes `fused_chunk`→`chunk_id` (it is an id-only reference; the `sources[]`
provenance does not fork the type name, so `fusion→chunk_read→synthesis` type-checks), retiring `fused_chunk`
(vocabulary down to 6 names). Applied + all 15 manifests re-emitted to `~/.air/registry` (regenerable via
`scripts/publish_manifests.py`). **Dep:** T6 (registration/author path), T38 (chunk_read, one of the 7),
ADR-0005 (the schema mirror this coordinates against).

### Task T34: Document update/upsert path (finding, logged during T17) — later

**Description:** Confirmed during the T17 rebuild (2026-07-15): **no document-level update/upsert path
exists**. The store has `upsert_chunk` (per-chunk) and `write_document` (a document's chunks, content-hash
gated) but **no delete-by-document**. On a document *change* the content hash changes → new `chunk_id`s →
`write_document` upserts the new chunks while the **old chunks orphan** (and their graph nodes + index
entries with them). This delete-and-re-chunk route is the only way a document is ever chunked more than
once, and it is what makes the "chunk once, persist, never recompute" ingestion lifecycle complete.

**Scope (when built, not now):** on a changed document, delete all existing chunks for that `source_doc_id`
(and their graph nodes, hybrid-index entries, **and their chunk-text sidecar entries — `ChunkTextStore.delete_document`, T40**),
then re-chunk and re-insert from scratch. Touches chunking (T17), the chunk write/store (T20, ArcadeDB
`store/`), the chunk-text sidecar (T40), and the graph layer (T25). Needs a delete-by-`source_doc_id` on
the store seam + graph (+ the sidecar's `delete_document`, already built), wired into a document-update
entry point.

**Why the sidecar raises T34's priority (T40, 2026-07-17):** a re-chunk already orphans index entries today,
so the sidecar orphaning text is the *same existing gap*, cleaned together when T34 lands — nothing
new-orphans that wasn't already. But the orphaned thing differs in kind: an orphaned index entry is a
stale summary + vectors (a **correctness** problem), while an orphaned sidecar entry is stale **full text**
(correctness **plus unbounded storage growth** — text is far larger than a summary and accumulates one copy
per re-chunked version of every updated doc). So T34 becomes **the thing that bounds sidecar storage growth**.
**Inertness for this milestone is conditional:** the ACORD eval corpus is ingested once and never updated,
so no orphaning triggers and T34 stays correctly deferred — but that inertness depends on the corpus not
being updated. On an **updating corpus, T34 is required, not optional** (fine for the eval, required for
production-with-updates).

**Status:** todo (finding — not a T17 blocker; makes the ingestion lifecycle complete; bounds T40 sidecar
growth on an updating corpus). **Dep:** T17, T20, T25, T40.

### Task T37: Deep-recursion completeness — structural coverage guarantee (T36 finding)

**Finding (GraphWright bind_run, 2026-07-17):** with the working set genuinely out of context (prompt-render
cut, delivery only via `tools.workingSet()`), the real RLM skill's deep-recursion completeness is variable
— full leaf coverage only ~half the time, sometimes silently missing a deep leaf while reporting success
(same silent-under-performance class as the earlier RLM findings). Masked earlier because step-1 delivery
was additive (working set in tool AND prompt), so the model could reach coverage by reading the prompt
without depending on recursion. **My tests had the same masking:** T28's opaque proof is hermetic (a fake
orchestrator emits a fixed correct workflow — never tested real-model completeness); T28/T17 live tests use
small/shallow sets. My synthetic reproduction (clean probe binary tree) is 5/5 even at depth 4 — structurally
easier than the real run, so it does not reproduce the ~50%. The finding is real (GraphWright measured it on
the real skill).

**Fix — the coverage guarantee belongs in the skill's interpreter WORKFLOW** (GraphWright's layering
correction): coverage-repair is *capability semantics*, so it must not live in the compiler's node
(domain-coupling the two-repo split forbids) nor in RAG_Wright's Python wrapper (doesn't run in the node).
The skill's workflow runs in the node by construction AND in this harness — one guarantee, every consumer.
- [x] **Grounded in-interpreter-expressible** (the analogue of the truncation/PTC-only checks): a spike ran
  an incomplete descent, then the coverage tail (JS) diffed `tools.workingSet()` against a `handled` set and
  re-dispatched the missed — all in interpreter code before returning.
- [x] **Moved into the workflow.** `RLM_WORKFLOW_JS` (and SKILL.md's canonical workflow) now: works over an
  item-list (each item has an `id`), the decomposer returns partition `cuts`, and after the recursion a
  **coverage tail** diffs the working set against `handled` and dispatches any missed item — code checking
  coverage, before returning. Chunking's discovery instructions carry the span-gap coverage tail; synthesis
  reads the method's tail. The working-set tools now expose `id` (chunking: item index; synthesis: chunk_id).
- [x] **Removed the Python wrapper guarantee** (`_guarantee_coverage`/`Synthesizer.extract` from synthesis;
  `_repair_partition` from chunking) — moved, not duplicated. `_validate_partition` stays as a loud check.
- [x] **Proven deterministically:** `test_coverage_tail_covers_an_incomplete_descent_in_the_interpreter` —
  a workflow whose descent handles only half the items reaches full coverage via the tail (6/6, 3 descent +
  3 tail). Full suite 384 passed + 26 skipped.

**bind_run findings (GraphWright, 2026-07-17) — the masking moved one level down, to recursion depth:**
- **Finding 1 — coverage passes FLAT.** The flat item-list + cuts made coverage trivially satisfiable (no
  deep leaves to miss), so a run that does NO divide-and-conquer (one worker over all items,
  maxSplitDepth=0, tail-fire=0) still passes the coverage bar. GraphWright saw 1/5 runs go fully flat with
  full coverage. **The honest signal is maxSplitDepth, not coverage.** At production scale a flat chunking
  run = one worker over the whole document = context overflow, RLM defeated, invisible on small test sets.
- **Finding 2 — root cause (confirmed).** SKILL.md dispatched the decomposer as `"decompose depth D"` with
  **no item count**, while `RLM_WORKFLOW_JS` (what the deterministic tests ran) sent `"...over N items"`.
  The decomposer needs the count to return index `cuts`, so a model following SKILL.md **literally can't
  partition and falls flat**; the count-bearing variant recurses. The green test validated a **different
  artifact** than what ships.

**Fix (landed):**
- [x] **Eliminated the drift:** SKILL.md's canonical workflow is now **byte-identical to `RLM_WORKFLOW_JS`**
  (count included), asserted by `test_skill_md_canonical_workflow_is_byte_identical_to_rlm_workflow_js` — so
  skill-vs-tested-artifact drift fails a test here, not at GraphWright's node.
- [x] **Depth signal in validation:** the recursion test asserts `maxSplitDepth >= 1` (recursion happened),
  the flat-teeth test asserts `< 1` (flatness caught) — not just coverage.
- [x] **Re-validated the real model reliably RECURSES out of context:** a real deepseek following the aligned
  skill on an 8-item set = **5/5 genuine recursion** (maxDepth=3, 15 decomposer dispatches), vs GraphWright's
  1/5 flat on the old skill. Full suite **385 passed + 26 skipped**.

**Acceptance bar (updated by GraphWright):** genuine recursion RELIABLY — `maxSplitDepth > 0` on a working
set that requires decomposition — measured by GraphWright's cut-prompt bind_run against the aligned skill,
NOT coverage (coverage was shown to pass flat).

**bind_run finding 3 (2026-07-17) — the silent drop moved to the LOAD boundary:** with delivery cut
clean (channel proven to fill the field with all 8), on ~1/11 runs the synthesis model **under-loaded** at
`const workingSet = await tools.workingSet()` — it read 3 of 8. The channel delivered 8 (deterministic);
the model loaded 3. The **coverage tail cannot catch this**: the tail guarantees coverage over what was
loaded, so with 3 loaded it faithfully covers those 3 and reports `missed=0`, success. The under-load is
**upstream of every guarantee** (delivery proven, coverage proven, evidence still vanishes) — the same
silent class one step earlier, at the load boundary.

**Fix (load-completeness assertion, mirroring the coverage tail at the other end):**
- [x] The workflow, right after the load, verifies it loaded the whole delivered set:
  `const _delivered = await tools.workingSetSize(); if (workingSet.length !== _delivered) throw`. The size
  is a **scalar from the runtime the model cannot under-read**, so an under-load fails **loud** before any
  leaf runs over the truncated set — silent evidence-drop → visible error. In `RLM_WORKFLOW_JS` + SKILL.md
  (byte-identical, drift-tested) and in chunking's discovery instructions. Both RLM nodes get it.
- [x] A `working_set_size` PTC tool added to synthesis + chunking (the truthful delivered count).
- [x] Test `test_load_assertion_fails_loud_on_an_under_read` — working_set returns 3 while size reports 8:
  the assertion throws loud, no leaf runs. Full suite **386 passed + 26 skipped**.
- [x] **Mechanical vs elective (GraphWright's question): ELECTIVE.** Dumped the real model's eval code —
  it loads the full set then writes `workingSet.slice(0, 2)`: it electively **samples/subsets** (here a
  diagnostic, but the exact tendency that lands on the processing path as "read 3 of 8"). The channel is
  not truncating; the model elects. So the fix is **assertion-plus-instruction** (both landed): the
  instruction prevents the elective subset, the assertion enforces it (fails loud on any residual
  under-read, elective or mechanical). The dump confirms the aligned skill is followed (full load +
  `workingSetSize`).
- Fixed a broken `skills/rlm/__init__.py` (externally added; absolute `from skills...` → `from rag_wright...`).

**Status:** DONE, both sides (2026-07-17). GraphWright's node-side depth-and-load bind_run against the
aligned skill passed **6/6 full-load-and-full-depth**. Skill side: drift eliminated (SKILL.md ==
RLM_WORKFLOW_JS, byte-identical, drift-tested), count fixed, depth signal, load-completeness assertion,
elective-subset instruction. The whole RLM arc (delivery, load faithfulness, recursion depth, coverage,
cross-node flow) is proven on both sides. **Dep:** T36.

### Task T35: Per-process interpreter-session serialization (KI-1 cross-graph constraint) — ADR-0020

**Description:** GraphWright's KI-1 resolution (2026-07-16): two QuickJS interpreter runtimes coexisting
in one process race on shared Rust-side state and **silently complete without dispatching ~half the
time, with zero exceptions**. GraphWright's compile-time guard rejects two interpreter nodes within one
graph, so a single ingestion graph is safe (one `rlm_chunking` interpreter session, concurrent workers
inside = reliable single-session case). What its guard cannot see: a harness running **multiple
documents' graphs concurrently in one process** brings up coexisting sessions across graphs — the exact
race. This is harness-level, so ours to enforce (ADR-0020).

**Current state — SATISFIED, with an ALWAYS-ON GUARD now in place.** The harnesses are serial (plain
`for d in docs:` loops), so KI-1 cannot bite today. AND, because the failure is silent, the enforcement is
**always on**, not deferred to this task: `skills/rlm/agent.py` holds a process-wide `BoundedSemaphore(1)`
+ `rlm_interpreter_session()` that serializes the **full** interpreter-session lifetime (build → run →
`_registry.close()` teardown, all in-lock) for both the chunking discoverer and the synthesis extractor.
Uncontended (zero cost) while serial; if concurrency is ever added without designing T35, it turns a
silent-correctness failure into a visible-performance one (serialized, slower, noticed) rather than
quietly-degraded chunks. **Load-bearing — not to be "cleaned up"** (ADR-0020). Tested: sessions never
overlap across threads; registry torn down on exit.

**T35's job is THROUGHPUT design, not correctness rescue.** The semaphore is the correctness floor; T35
designs the real concurrent batch path (batch sizes, worker counts, OQ8) that serializes interpreter
sessions **consciously**, and carries a **fail-if-silent** check — that dispatch actually FIRED under
concurrency, not merely that the run completed — because the failure has no error signal. Everything else
(parse, embed, summarize, write) may stay concurrent. Closes the interpreter-concurrency dimension of **OQ8**.

**Exit path:** lifted when the upstream coexistence bug (langchain-quickjs/deepagents) is fixed — or via a
sandbox-based RLM build / RLM-in-LangGraph-via-DSPy — verified by the KI-1 regression harness at full
dispatch under concurrency. Then this and GraphWright's in-graph guard lift together.

**Status:** todo (binding constraint; satisfied while the harness is serial). **Dep:** T17, T28.

### Task T36: Working-set delivered by a runtime tool, not message-embedded JSON (GraphWright contract)

**Description:** Cross-repo contract from GraphWright (docs/handoff/working-set-binding.md, 2026-07-16).
Today RAG_Wright delivers the working set **in the model's context**: rlm_synthesis embeds the candidate
set as JSON in the HumanMessage; rlm_chunking embeds an item-view (metadata + 80-char previews) + a `peek`
tool. That partially defeats RLM (the model should hold interpreter variables, not the whole set). Fix:
GraphWright binds a deterministic PTC tool returning the node's channel input, exposed **inside the
interpreter** as `globalThis.tools.workingSet()` (host tool name `working_set`; grounded:
`to_camel_case('working_set')='workingSet'`, PTC exposes `tools.<camel>` async, `task` excluded from PTC
so no collision). The result stays a JS value, **never enters context** — runtime-guaranteed wiring, same
principle as the workflow trigger and eager method load.

**Scope (pending GraphWright binding the tool):**
- SKILL.md canonical workflow becomes: `const workingSet = await tools.workingSet(); decompose(workingSet, 0);`
  — replaces the `WORKING_SET`-binding assumption.
- rlm_synthesis: drop JSON-in-HumanMessage; the extractor binds `working_set` (PTC) returning the
  candidates in RAG_Wright's own harness (GraphWright binds it from the channel in production).
- rlm_chunking: drop item-view-in-message; `peek` is **subsumed** (the model reads item text from the
  returned JS value); the discoverer binds `working_set` returning the document items.
- **Re-validation, NOT a free swap:** re-run **T28**'s opaque-candidate-set recursion + citation-preservation
  proof AND **T17**'s coherent-clause boundary-quality proof under tool-based delivery (both proofs ran
  against the OLD message-embedded seam). Also validate a **large** working set via the tool does not hit a
  PTC result-size cap (`max_result_chars`), since the whole point is a big value that stays in JS.
- **Skill rename (cosmetic, folded in):** frontmatter `name: rlm-method` → `name: rlm` to match the `rlm`
  directory (Agent Skills spec compliance). Directory can't be `rlm-method` — `rag_wright.skills.rlm` is an
  imported Python package and module names can't contain hyphens. Bundled here since it also touches SKILL.md.

**Status:** done (2026-07-16). GraphWright's bind landed; implemented + re-validated. **Dep:** T17, T28.
**Re-validation (green):** T28 opaque-recursion + citations and T17 coherent-clause-kept-whole both **passed live** under tool-delivery (real model calls `tools.workingSet()` and follows the method); no-truncation is a committed hermetic test (300-item doc + full last-item text intact via the tool). Grounded PTC-only exposure: `working_set` is exposed as `tools.workingSet()` inside `eval` and is NOT a top-level tool, so the working set never enters context. Full suite 383 passed + 26 skipped.

### Task T-CHK: RLM chunker degenerate-split fix (T17 bug) — ask-first

**Description:** GATE-2 run 1 surfaced degenerate RLM chunking: `_split_into_chunks` flushes at every
header item with no minimum-chunk-size floor and no tiny-section coalescing, so header-dense/OCR'd docs
over-fragment (ASIANDRAGON 113 chunks/10k tok; ADAMSGOLF 49 chunks/24KB) and emit near-empty heading-only
chunks (`_validate_boundaries` only rejects fully-empty text). This depresses RLM retrieval independently
of the query artifact — must be fixed before any RLM-vs-baseline comparison so a chunker bug is not
attributed to the chunking strategy. Fix: a min-size floor / merge tiny adjacent sections; reject
near-empty chunks in validation. Changes an approved capability → **ask-first**.

**RAC-CHK:**
- [x] Min-size floor (`MIN_CHUNK_CHARS=1000`, ~250 tokens) via forward accumulation: a heading starts a
  new chunk only once the current chunk meets the floor AND the heading is a *major* boundary
  (same-or-higher level than the section the chunk opened with). Below-floor or deeper-subsection
  headings keep accumulating.
- [x] **Hierarchy-preserving** (per review constraint): a deeper subsection is never split away from its
  parent (test: level-2 subsection stays in the level-1 parent's chunk); tiny sections fold into a
  neighbour within the same parent (`_merge_below_floor`, backward-first, never over the cap), never by
  destroying a boundary. A real section break still splits when both sides meet the floor.
- [x] Validation rejects near-empty/heading-only chunks (`_validate_boundaries`, `<20` chars when >1
  chunk); a lone short chunk (short doc) is allowed. Cap guarantees preserved (hard-split unchanged).
- [x] Regression fixture on the ASIANDRAGON pathology (synthetic 112 level-1 headings) + an opt-in real-doc
  check on the cached parse. **Real ASIANDRAGON: 113 → 34 chunks, char min 1006 (was 10 near-empty),
  median 1141, all ≥ floor.** Distribution (min/median/max) reported.
- [x] Summarizer text-fallbacks re-checked at re-ingest: **0 fallbacks** with the fixed chunker (run 1
  had 7). Confirms the fallbacks were a symptom of the split bug (near-empty text → structured None), not
  a separate failure. Re-ingest chunk distribution: total 296 (was 632), median 9/doc, **no near-empty**.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (12 passed incl. real ASIANDRAGON
regression; 2 opt-in skipped). Full suite 300 passed + 16 skipped.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (`MIN_CHUNK_CHARS`, hierarchy-aware
`_split_into_chunks`, `_merge_below_floor`, near-empty validation), `tests/capabilities/test_rlm_chunking.py`.
**Consequence recorded:** GATE-1's RLM numbers marked **superseded** (measured on buggy chunks; ADR-0009
keeps RLM regardless). Any post-T-CHK A/B needs a full re-ingest (stored chunks stale).
**Status:** done (fallback re-check + per-doc signal at the re-ingest step, next). **Dep:** T17.

### Task T-SUM: Concurrent summarization (T17 enhancement)

**Description:** RLM `chunk()` summarized chunks in a serial loop — 900 chunks × ~4s serial OpenRouter
calls ≈ 1 hour, the re-ingest bottleneck (Docling was cached; no rate-limit in our code). Applied the
embedding capability's proven async+backpressure pattern (T19, FR-I.6) to summarization: `_summarize_all`
runs each blocking `summarize()` in a thread (`asyncio.to_thread`), bounded by an `asyncio.Semaphore`
(`DEFAULT_SUMMARY_CONCURRENCY=8`); `gather` preserves order so chunking stays deterministic (each
summary is independent of concurrency). `chunk()` stays synchronous (internal `asyncio.run`), so no
caller changes.

**RAC-SUM:**
- [x] Summaries run concurrently, bounded by a semaphore. Hermetic: `max_inflight == 4` at cap 4, `== 1`
  at cap 1 (strictly serial); order preserved (determinism holds; `chunk twice → identical` still passes).
- [x] **Validated with real LLM calls** (the T17 lesson — stubs are not enough): live `-m model` test,
  12 real DeepSeek summaries, **concurrent(8)=8.1s vs serial(1)=27.1s = 3.4× speedup**, thread-safe, no
  throttling errors, all summaries valid.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py` (14 passed + 2 opt-in skipped;
full suite 302 passed + 16 skipped); live `... -m model` (3.4× speedup).
**Files:** `src/rag_wright/capabilities/rlm_chunking.py` (`_summarize_all`, concurrent `chunk()`,
`DEFAULT_SUMMARY_CONCURRENCY`), `tests/capabilities/test_rlm_chunking.py` (thread-safe stub + concurrency
+ live-speedup tests). **Status:** done. **Dep:** T17.

---

## Phase 4 — Build, graph layer (spec Phase 2)

### Task T23: Graph extraction (contract + spaCy NER/dependency; LLM escalation)

**Description:** A hybrid stack over the parsed documents (T16), all conforming to one ontology
(T4/T8): Pydantic-contract extraction for schema entities, a lightweight NER-plus-dependency path
(spaCy) for the bulk, and an open-ended language-model escalation for hard cases (FR-C.6, FR-I.4).
OpenIE stays deferred behind the T5 extractor seam (risk 10; adding it is ask-first). This is a
GPU-calling capability (the LLM escalation and spaCy pipelines), so it carries the FR-I.6
decoupling property.

**RAC-23:**
- [x] Contract extraction, the spaCy NER path, and the LLM escalation each produce ontology-conforming
  facts carrying `chunk_id` + confidence. **`SpacyNerExtractor`** emits typed mentions only
  (ORG→ORGANIZATION, PERSON→PERSON, EXTRACTED) — **no proximity edges** (ADR-0012; `EntityMention` gained
  a `confidence` field). **`ContractExtractor`** emits ClauseFacts + party mentions + **CONTRACTS_WITH
  from the signing-party structure** (the only source of that edge, EXTRACTED). **`LlmEscalationExtractor`**
  emits hard-case RelationshipFacts (INFERRED). All anchored to `chunk_id` via `ExtractionResult`/`Provenance`.
- [x] The LLM extractors call the model only through the seam under the DeepSeek V4 Pro
  `STRUCTURED_REASONING` profile (ADR-0006); no provider/model flag in the code. **Live `-m model`: real
  DeepSeek round-trips the 41-value `ClauseCategory` enum + `RelationshipType` schemas.**
- [x] **FR-I.6 decoupling:** `extract_chunks` is async; each chunk's stack runs in `asyncio.to_thread`
  bounded by a semaphore. Concurrency test: `max_inflight == 4` at cap 4, `== 1` serial.
- [x] Registered under FR-C.6. `register_graph_extraction` → `graph_extraction`, `function`, contract
  `ExtractionResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/graph_extraction.json`); mirror
  conformance green; re-validates as a `RegistryEntry`. Live `RegistryStore` load is the GraphWright step.

**Verification:** `uv run pytest tests/capabilities/test_graph_extraction.py` (10 hermetic passed); live:
`-m ner` (real spaCy, 1 passed) and `-m model` (real DeepSeek contract + escalation, 2 passed). Full suite
313 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T5, T8, T16. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_extraction.py`, `tests/capabilities/test_graph_extraction.py`,
`src/rag_wright/contracts/extraction.py` (`EntityMention.confidence`, ADR-0012),
`tests/contracts/test_extraction.py`, `src/rag_wright/capabilities/manifests.py` (+`graph_extraction`),
`pyproject.toml`/`uv.lock` (`en_core_web_sm` MIT, wheel-pinned) + `conftest.py` (`ner` marker),
`docs/adr/0012-entity-mention-confidence-no-proximity-edges.md`.
**Note:** Co-occurrence edges rejected as false-edge generators — CONTRACTS_WITH from party structure only
(ADR-0012), because T26 surfaces but does not filter by confidence (FR-C.5/FR-Q.3). spaCy model is
config-driven (`RAG_SPACY_MODEL`, swappable md/lg/trf, not trf); OntoNotes NER noise is expected and
handled by T23b + the human gate. OpenIE still slots behind the T5 seam later (ADR-0001).

### Task T23b: Entity disambiguation and canonicalization (normalize, reject, cluster)

**Description:** Between recognition (T23) and linking (T24), turn raw extracted party and entity
mentions into canonical mention clusters: normalize surface forms (legal-suffix and
whitespace/punctuation canonicalization), reject non-entities (template placeholders, role
artifacts, over-broad or degenerate matches), and cluster the survivors that denote one real-world
entity (blocking plus similarity). The output is a set of canonical clusters, each a proposal a
human verifies, that T24 then links to an EDGAR CIK. This is the canonicalization stage FR-C.7
resolution presupposes, and it keeps human name-to-CIK verification (SPEC §14) scaling with entity
count, not mention count. Full coreference (pronouns, definite descriptions) is deferred behind a
real seam, the same discipline as OpenIE at T5.

**RAC-23b:**
- [x] Normalization (ADR-0004 N1-N5): reuses the T10 `corpus.canonicalize.normalize_entity_name`
  (legal-suffix, whitespace/punctuation, possessive-apostrophe, NFKC). Test: the three "Bank of America"
  variants collapse to one cluster; "Stremick's"/"Stremicks" collapse.
- [x] Rejection filter (ADR-0004 R1-R5): reuses `corpus.canonicalize.is_entity`. Test: placeholder,
  role artifact, bare generic ("Services"), and alias-only ("formerly known as Tradeum, Inc.") are
  rejected (in `DisambiguationResult.rejected`, never a cluster); "Acme Inc. d/b/a SuperBrand" recovers
  "Acme".
- [x] Clustering (ADR-0004 C3/C4): grouped by (normalized key, entity_type); ambiguous near-duplicates
  **flagged, never merged** — `_flag_near_duplicates` sets `ambiguous_with` for proper-token-subset or
  shared-first-token pairs. Test: ScanSource / ScanSource Latin America and Armstrong Flooring / Armstrong
  Hardwood Flooring stay 4 clusters, each flagged; Bank of America / Bank of England neither merged nor
  flagged. Cluster precision/recall == 1.0 on a labeled fixture.
- [x] Regression fixtures cover the T10 misses: possessive-apostrophe merge, bare-generic reject,
  alias-prefix reject.
- [x] Output `MentionCluster`s carry `chunk_id` provenance (the chunks the mentions came from) and the
  **weakest** confidence over the cluster (test: an AMBIGUOUS member → AMBIGUOUS cluster), shaped as
  proposals (`ambiguous_with` = human decision points), never auto-committed merges.
- [x] Full-coreference `CoreferenceResolver` seam (deferred, mirrors T5): `disambiguate(...,
  coreference_resolvers=...)`; a stub resolver bound in test rewrites the cluster set (load-bearing);
  default is no resolvers.
- [x] Registered under `entity_disambiguation` (FR-C.7); contract `DisambiguationResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/entity_disambiguation.json`);
  mirror conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_disambiguation.py` (11 passed, hermetic). Full
suite 324 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T23. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/disambiguation.py`,
`tests/capabilities/test_disambiguation.py`, `src/rag_wright/capabilities/manifests.py`
(+`entity_disambiguation`), `docs/adr/0004-entity-disambiguation.md` (existing; honored).
Reuses `src/rag_wright/corpus/canonicalize.py` (T10) — normalize/reject/cluster kept, not rewritten.
**Note:** Recognition (T23) and linking (T24) already existed; this fills the canonicalization gap
between them that the T10 data exposed (Bank-of-America variant fragmentation, template and
role-artifact noise). It converts the human from cluster generator to proposal verifier, which is
what makes the ground-truth discipline scale. Folded under FR-C.7 in the coverage map; see the SPEC
note below. **The normalize/reject/cluster rules were first built and human-verified at T10** in
`src/rag_wright/corpus/canonicalize.py` (normalize_entity_name / is_entity / cluster_entities, 17
tests); T23b hardens them into the capability behind the extractor/coreference seam.
The normalize and reject rules are recorded in ADR-0004 and are corpus-derived: the possessive-apostrophe rule (Stremick's equals Stremicks), the bare-generic-token reject ("Services", "Bank"), and the alias-prefix reject ("formerly known as", "d/b/a") each came from a real T10 verification-set miss.

### Task T24: Entity resolution (closed-world to EDGAR CIK)

Description: Resolve the canonical mention clusters from T23b to the registry's canonical entity_id (EDGAR CIK), closed-world against the known set (FR-C.7). Surface-form fragmentation is handled upstream at T23b, so this task links a clean cluster to a CIK rather than fighting variants. Decide and record the matching strategy (exact / fuzzy / embedding / LLM-assisted, §16.3) at this task.

**RAC-24:**
- [x] A known cluster resolves to the correct EDGAR CIK `entity_id`; an unknown resolves to `None`
  (closed-world, not fabricated). `resolve_entities` links each `MentionCluster` via
  `registry.resolve` (representative then variants, first hit). Test: "Acme Corporation" → CIK, a
  private co → None, and an alias variant ("Acme Inc") links.
- [x] Fragmentation rate is **measured** — `fragmentation_rate(result, gold_by_key)` = fraction of true
  entities ending as >1 node. Test: two clusters T23b left separate both link to one CIK → 0.0
  (resolution reduces fragmentation via alias linking); a split entity → 1.0. Full golden-set run is
  eval-time (like recall).
- [x] **Post-resolution self-loop check** (moved from the T4 contract, needs resolved ids): a
  relationship whose two *distinct* refs resolve to the same non-None `entity_id` is dropped. Test:
  "Acme Corporation" AFFILIATE_OF "Acme Inc" (both → 0000000001) → dropped; two unlinked refs → kept.
- [x] **Both mention channels as one stream:** a relationship ref resolves by matching a cluster key
  first (taking that cluster's id, even if None), falling back to the registry only for a cluster-less
  ref — so an entity as both a standalone mention and a relationship endpoint is one node. Test: the
  CONTRACTS_WITH refs take the standalone Acme/Beta cluster ids.
- [x] Matching strategy recorded: **ADR-0013** — exact normalized, closed-world, no fuzzy/embedding/LLM
  (conservative-merge bias; a wrong fuzzy link is a silent false merge). Fuzzy is a later, eval-gated
  option behind the same `resolve` boundary.
- [x] Registered under `entity_resolution` (FR-C.7); contract `ResolutionResult`. ARD manifest present
  in the shared root (`~/.air/registry/entity_resolution.json`); mirror conformance green.

**Verification:** `uv run pytest tests/capabilities/test_entity_resolution.py` (9 passed, hermetic). Full
suite 333 passed + 20 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T8, T23b. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/entity_resolution.py`,
`tests/capabilities/test_entity_resolution.py`, `src/rag_wright/capabilities/manifests.py`
(+`entity_resolution`), `docs/adr/0013-entity-resolution-matching-strategy.md`.
**Note:** Resolves §16.3 (ADR-0013). Fragmentation is risk 5. **ADR is 0013**, not the ledger's earlier
"0005-entity-resolution.md" reference (0005 is the relational golden set).

### Task T25: Graph storage (nodes/edges carry `chunk_id`, content-hash gated)

**Description:** Write nodes and edges into the same ArcadeDB store, carrying the originating
`chunk_id`, conforming to one ontology, gated by content hash (FR-I.4, FR-I.5). A chunk and its
extracted entities connect in one transaction (FR-S.1). The graph is the relationship layer only;
heavy structured data does not go in it (SPEC §8).

**RAC-25:**
- [x] Nodes/edges written carrying `chunk_id` + confidence; a chunk and its entities land in **one
  transaction** (`DatabaseDao.execute_transaction`). Entity nodes upsert by `node_key` (CIK when linked,
  `UNLINKED:<key>` surrogate otherwise; `cik`/name/type/confidence/chunk_id props); `Relationship` edges
  connect resolved node keys; `Mentions` edges connect each `Chunk` to its `Entity` (FR-S.1). Live
  (`-m store`): 2 nodes + 1 relationship + 2 Mentions created in one transaction. Ref-only endpoints get
  a minimal node so every edge connects.
- [x] Content-hash gate: a per-document checkpoint keyed by content hash makes an unchanged re-run a
  no-op (`skipped`, no store write, no duplicate edges). Live-verified; hermetic test proves the store is
  written exactly once; a changed hash re-writes.
- [x] Relationship layer only (SPEC §8): nodes carry id/name/type/confidence, edges carry the
  relationship — no heavy structured data.
- **(No ARD bullet.)** Seam-bound ingestion step, no §5 slug (FR-I.4) → registers nothing, no manifest
  (per the ARD-registration rule). Writes through the T13 `Store` seam (extended: `write_graph`,
  `graph_counts`, + `GraphNode`/`GraphEdge`).

**Verification:** `uv run pytest tests/capabilities/test_graph_storage.py` (5 hermetic passed); live
`-m store` (1 passed: real transaction, nodes/edges/Mentions, gate); T13/T14 store tests still green with
the extended `Entity` schema. Full suite 338 passed + 21 skipped.

**Dependencies:** T13, T23, T24. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_storage.py`, `tests/capabilities/test_graph_storage.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (graph-write seam: `write_graph`/`graph_counts`,
`GraphNode`/`GraphEdge`, `Entity` props + `Relationship`/`Mentions` edge types),
`tests/store/test_arcadedb_schema.py` (stub extended to the graph-write seam).

### Task T26: Graph query (cited `chunk_id`s, `entity_id`s, confidence)

**Description:** Answer relational and multi-hop questions via graph query, returning an answer
with cited `chunk_id`s, `entity_id`s, and confidence tags, from the same store as the retrieval
index; the answer is treated as evidence, not truth (FR-C.5, FR-Q.3, SPEC §8/§14).

**RAC-26:**
- [x] A relational/multi-hop question returns an answer with cited `chunk_id`s, `entity_id`s, and
  confidence tags. `graph_query(start_entity_id, *, store, relationship_type, max_hops)` traverses via
  the store's `graph_neighbors` (ArcadeDB `MATCH` over `Relationship` edges — `bothE` cites each edge's
  `chunk_id`/`confidence`, `bothV` the reached entity; one-hop + two-hop, `$matched` de-dup; grounded
  live). Returns a `GraphAnswer` of `GraphEvidence` (entity_id, name, `path_entity_ids`, `chunk_ids`,
  `confidences`, hops). **Live (`-m store`): one-hop co-parties (B, C) cited from their edges; two-hop
  A→B→D with the full path `[A,B,D]` and both edges' chunks.** Confidence is **surfaced, not filtered**
  (FR-C.5/FR-Q.3); gating on it is the generator's job (FR-Q.6/T29). Full golden-set run is eval-time.
- [x] Shaped as evidence for fusion (T27), not a final ranked list: `GraphAnswer.evidence` is an unranked
  list of cited candidates.
- [x] Registered under FR-C.5. `register_graph_query` → `graph_query`, `function`, contract `GraphAnswer`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/graph_query.json`); mirror
  conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_graph_query.py` (4 hermetic passed); live
`-m store` (2 passed: real one-hop + two-hop MATCH). Full suite 343 passed + 23 skipped. Publish:
`uv run python scripts/publish_manifests.py`.

**Dependencies:** T25. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/capabilities/graph_query.py`, `tests/capabilities/test_graph_query.py`,
`src/rag_wright/store/{seam,arcadedb}.py` (`graph_neighbors` traversal), `manifests.py`
(+`graph_query`), `tests/store/test_arcadedb_schema.py` (stub extended with in-memory traversal).

### Task T27: Fusion (union/dedup on `chunk_id`, capped)

**Description:** Union and deduplicate on `chunk_id` between the reranked top set (T22) and the
graph-cited chunks (T26), capped; not a score fusion, since the graph returns an answer, not a
comparable ranked list (FR-Q.4).

**RAC-27:**
- [x] The reranked top set (T22 `RerankResult`) and graph-cited chunks (T26 `GraphAnswer` evidence) are
  unioned and deduplicated on `chunk_id`, capped (`DEFAULT_UNION_CAP=20`). `fuse(reranked, graph, *, cap)`
  → `FusionResult` of `FusedChunk{chunk_id, sources}`. Test: retrieval [c1,c2] + graph [c2,c3] → [c1,c2,c3],
  c2 tagged both sources; cap cuts the union.
- [x] Deterministic (retrieval order first, then graph first-appearance; same inputs → identical output)
  and **not a score fusion** — `FusedChunk` carries `sources`, no score (the graph returns an answer, not
  a comparable ranked list).
- [x] Registered under FR-Q.4. `register_fusion` → `fusion`, `function`, contract `FusionResult`. ARD
  manifest present in the shared root (`~/.air/registry/fusion.json`); mirror conformance green.

**Verification:** `uv run pytest tests/capabilities/test_fusion.py` (6 passed, hermetic). Full suite 350
passed + 23 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T22, T26. **Scope:** S. **Status:** done.
**Files:** `src/rag_wright/capabilities/fusion.py`, `tests/capabilities/test_fusion.py`,
`src/rag_wright/capabilities/manifests.py` (+`fusion`).

---

## Phase 4 — Build, RLM synthesis tier (spec Phase 3)

### Task T28 (REOPENED 2026-07-15, rebuild): RLM synthesis = recursive descent + kept _reduce ascent

**Description:** The query-side RLM rebuild (ADR-0015/0016). Two halves: **descent** (new, recursive) — a
`SliceExtractor` seam (`SeamSliceExtractor` via `build_rlm_agent`) decomposes the candidate set through
the T15 machinery (fresh `rlm_decomposer` per over-large group, `rlm_slice_worker` extracts query-relevant
facts per leaf; per-slice tools/skills live in the worker); **ascent** (kept, ADR-0016) — the Python
`_reduce` fan-in combines the extracts into the synthesis. Unlike chunking, **recursion IS gated** here
(ADR-0019). Every `SliceOutput` keeps its `chunk_id` (no claim without a citation, FR-Q.6).

**RAC-28 (rebuild):**
- [x] Descent + ascent compose: `rlm_synthesize` = `extractor.extract` then `_reduce`; cited `chunk_id`s;
  empty candidates → empty. Hermetic with a stub extractor + stub combine.
- [x] **Recursion GATED (ADR-0016/0019):** the descent recurses past depth one — an opaque candidate set
  (`C0`) whose leaves only the decomposer reveals forces re-entry; the decomposer fires at >1 depth,
  workers extract the leaves, dispatch is code-driven (`eval_id`, fail-if-sequential). Driven by scripted
  fake models through the real machinery.
- [x] **Per-slice tool use** in extraction: a worker invokes a `cite` tool mid-extraction.
- [x] **`_reduce` kept** exactly (the ascent): recursive fan-in, ≤ fanout per combine (9 extracts, fanout
  3 → 4 combine calls). No sub-call ever sees the whole set of extracts.
- [x] **Live** (`-m model`): real deepseek-v4-pro extracts from candidates ($500M, Delaware) + combines +
  preserves citations. **Passed.**

**Verification:** `uv run pytest tests/capabilities/test_rlm_synthesis.py` — 6 passed + 1 skipped (live);
`-m model` **passed**. ruff clean. Full suite **379 passed + 26 skipped**. No external callers of the
removed flat `synthesize_slice`/`rlm_synthesize_async`. Dropped the old flat-descent concurrency tests
(that path is replaced by the agent; the `_reduce` fan-in is retained + tested).

**Dependencies:** T15, T27, ADR-0019. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_synthesis.py`, `tests/capabilities/test_rlm_synthesis.py`.
**Rebuild complete:** T15→T17→T28 all rebuilt. **Follow-up done (separate commit):** populated
`grantedSubagents = ["rlm_decomposer","rlm_slice_worker"]` in the 3 RLM manifests (bound from the skill's
own `GRANTED_SUBAGENTS`, with a conformance test asserting manifest roster == skill roster so it cannot
drift), re-emitted to the shared root. **GraphWright handoff:** the sub-agents are real; GraphWright
re-runs mirror-vs-store verification and this unblocks its T9d.6 interpreter arm / W10.

### Task T28 (original, superseded by the rebuild above): RLM synthesis (interpreter load, slice in code, recursive sub-calls)

**Description:** Using the RLM skill (T15), load the candidate chunks (T27) into an interpreter as
data, slice and filter in code, and recursively call sub-models on the small focused portions, so
it never attends over the full chunk volume (FR-Q.5). The recursive sub-calls are
structured-under-reasoning work, so they resolve to the DeepSeek V4 Pro default in the seam.

**RAC-28:**
- [x] Candidate chunks load into the interpreter as data (a Python list of `SynthesisChunk`) and are
  sliced in code — one focused unit per chunk. `rlm_synthesize(query, chunks, *, synthesizer, ...)`.
- [x] Sub-model calls run on focused portions and the capability **never attends over the full volume**:
  each `synthesize_slice` sees one chunk; the code-side `_reduce` combines at most `fanout` notes per
  call, recursing. Test asserts each chunk is sliced alone and no combine sees > `fanout` notes (the
  reduce recursed). Dispatch is **concurrent + bounded** (async + `asyncio.Semaphore`, per the CLAUDE.md
  parallel-LLM rule; `max_inflight == N`). Sub-calls use the DeepSeek V4 Pro `STRUCTURED_REASONING`
  profile (ADR-0006); no model flag in code.
- [x] Registered under `rlm_synthesis` (FR-Q.5), kind **`agent_skill`** (applies the RLM method, requires
  `rlm_method`), contract `SynthesisResult`.
- [x] ARD-registered: manifest present in the shared root (`~/.air/registry/rlm_synthesis.json`,
  `requires: ['rlm_method']`); mirror conformance green; re-validates as a `RegistryEntry`.

**Verification:** `uv run pytest tests/capabilities/test_rlm_synthesis.py` (6 hermetic passed); live
`-m model` (1 passed, real DeepSeek, 3 concurrent slice calls + reduce in ~10s). Full suite 357 passed +
24 skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T15, T27. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/rlm_synthesis.py`, `tests/capabilities/test_rlm_synthesis.py`,
`src/rag_wright/capabilities/manifests.py` (+`rlm_synthesis`, requires rlm_method).

### Task T29: Answer generator (grounded, cited, abstains) + vision-to-text

**Description:** Produce grounded, cited answers (no citation, no claim), confidence-aware, that
abstain when the retrieved context does not support an answer (FR-Q.6); plus the image-and-scan-
to-text step at ingestion (FR-C.9), on a Gemma 4 class model through the model-profile seam.

**RAC-29:**
- [x] Every non-abstaining answer carries a citation, and an unsupported question abstains rather than
  fabricates — **enforced in code** around the model: empty evidence abstains without a model call;
  citations not in the evidence are dropped; an answer left with no valid citation is coerced to an
  abstention (FR-Q.6). `generate_answer(query, evidence, *, model) -> GeneratedAnswer{answer, citations,
  abstained}`. **Live (`-m model`): real Gemma answer cited only from the real evidence ids.**
- [x] Confidence-aware: graph-fact confidence tags are put in front of the generator (`_evidence_block`
  surfaces `[confidence: …]`); a test asserts the tag reaches the model's prompt.
- [x] Vision-to-text converts a scanned image to text at ingestion (`vision_to_text`, Gemma 4 multimodal
  via the seam). **Live: transcribed a synthetic PNG ("HELLO WORLD").**
- [x] Registered under FR-C.9 as **two** capabilities/slugs (split per GraphWright's RegistryStore
  verification, **ADR-0014**): `generation` (answer generation, contract `GeneratedAnswer`,
  `register_generation`) and `vision_to_text` (transcription, contract `VisionTranscription`,
  `register_vision_to_text`) — different inputs/callers/failure modes, so discovery ranks each on its own
  intents (a bundled slug diluted both).
- [x] ARD-registered: **both** the `generation` and `vision_to_text` manifests are present in the shared
  root (`~/.air/registry/{generation,vision_to_text}.json`), each with representative queries scoped to
  its own behavior; mirror conformance green; 14 manifests total. GraphWright re-verifies after the emit.

**Verification:** `uv run pytest tests/capabilities/test_answer_generator.py` (8 hermetic passed); live
`-m model` (2 passed: real Gemma generation + real image transcription). Full suite 366 passed + 26
skipped. Publish: `uv run python scripts/publish_manifests.py`.

**Dependencies:** T11, T27. **Scope:** L. **Status:** done.
**Files:** `src/rag_wright/capabilities/answer_generator.py` (`register_generation`),
`src/rag_wright/capabilities/vision_to_text.py` (`register_vision_to_text`, `VisionTranscription`),
`tests/capabilities/test_answer_generator.py`, `src/rag_wright/capabilities/registry.py` (+`vision_to_text`
slug), `src/rag_wright/capabilities/manifests.py` (+`generation`, +`vision_to_text`),
`docs/adr/0014-split-generation-and-vision-to-text.md`, SPEC §5 + FR-C.9 (split).
**Note:** Enforces "no claim without a citation" and abstention (risk 9). Vision-to-text exercises
the scanned-filing subset (ADR-0002). **FR-C.9 split into `generation` + `vision_to_text` (ADR-0014)**
after GraphWright's RegistryStore verification flagged discovery dilution from the bundled slug.

### Task T30: Prefix + result caching

**Description:** Add prefix/prompt caching of the code scaffold and interpreter state and
memoization of sub-call and graph results (SPEC §13 Phase 3, §16.7). Resolves the caching-design
open question at this task.

**RAC-30:**
- [ ] Repeated sub-calls / graph results are memoized (a cache hit does no model work).
- [ ] Caching does not change answer correctness or citations.

**Verification:** `uv run pytest tests/capabilities/test_caching.py`

**Dependencies:** T28, T29. **Scope:** M.
**Files:** `src/rag_wright/capabilities/caching.py`, `tests/capabilities/test_caching.py`
**Note:** Round-trip and tail latency are measured here to inform routing thresholds (routing
itself is orchestration; §16.6).

---

## Phase 5 — Integrate and end to end

### Task T31: MCP skill surface (governed skills over MCP)

**Description:** Expose the registered capabilities (T6) as governed skills over a Model Context
Protocol (MCP) interface, so agents call the component and do not own it (FR-S.5, SPEC §1).
Grounded surface: the official `mcp` SDK (ADR-0001).
**ARD manifest kind is not affected by this surface (GraphWright RegistryStore verification):** the ARD
manifest `kind` describes how the **compiler** binds a capability — the 10 non-RLM capabilities are
`function` because the compiler binds them as **in-process callables**, deliberately, not over MCP. T31's
MCP surface is a **separate exposure** of RAG_Wright's capabilities to external callers; it does **not**
change any manifest `kind`. Do not revisit `function` → `mcp_tool` when building this.

**RAC-31:**
- [ ] Retrieval and graph capabilities are reachable only through the query-skill interface over
  MCP; the store implementation stays swappable behind it.
- [ ] The surface grows from the capability registry (no capability hardcoded outside it).

**Verification:** `uv run pytest tests/mcp/test_skill_surface.py`

**Dependencies:** T6, T22, T26. **Scope:** M.
**Files:** `src/rag_wright/mcp/server.py`, `tests/mcp/test_skill_surface.py`
**Note:** This is the capability surface, not the ingestion/query graphs (those are compiled from
the Orchestration Spec).

### Task T32: End-to-end scenarios + per-source ablation

**Description:** Run the end-to-end golden scenarios across the four archetypes and the per-source
ablation (turn off text retrieval, then graph, then RLM synthesis; measure the loss by archetype),
confirming the acceptance bar from Phase 0 (SPEC §12, §15, plan §4).

**RAC-32:**
- [ ] End-to-end answers are correct, cited, and abstain when unsupported, across the four
  archetypes, at the Phase 0 bar.
- [ ] The per-source ablation shows the graph leg's contribution on the EDGAR-derived multi-hop
  questions (T10) — the leg the graph exists for.
- [ ] An unchanged corpus re-run does effectively no work (incremental-update success criterion).

**Verification:** `uv run pytest eval/test_end_to_end.py -m e2e`

**Dependencies:** T29, T31. **Scope:** L.
**Files:** `eval/test_end_to_end.py`, `eval/ablation.py`
**Note:** Final review gate for Phase 5.

### Checkpoint: Complete
- [ ] All capabilities built and tested; every **query-discovered §5 capability** is internally
  registered and ARD-registered (its manifest loads and validates under `RegistryStore`), while the
  **seam-bound write/store steps (chunk write T20, graph storage T25) register nothing** (they are
  wired by the compiler, not discovered). Both gates resolved and recorded. End-to-end bar met. Ready
  for the compiler to discover and bind the graphs from the Orchestration Spec.

---

## Phase 5 — FR-K: Embedding-free OKF navigation (experimental, gated)

Corpus-neutral capability; ACORD is the first validation corpus. The T41 diagnosis (query-representation
gap, strong clause-clause structure) is the motivation: OKF traversal is the one mechanism that never
computes a query-to-chunk similarity. Categories are **manufactured via `graph_extraction`** (the corpus
does not ship them; verified for ACORD, BEIR corpus is `{_id, text}` only). Index descriptions reuse
existing chunk summaries (T-SUM). The compiled bundle is a gitignored, rebuildable data artifact.
Grounding: OKF producer patterns ground against the cloned reference agent's `bundle/` modules
(`/Users/farhan/work/knowledge-catalog/okf/src/graphify-out/graph.json`: `OKFDocument.parse/serialize`,
`regenerate_indexes`, `concept_id_to_path`, `write_concept_doc`); the reference agent is Google ADK +
Gemini + BigQuery, not our stack, so the compiler is reimplemented on langchain_openai/deepagents and
only the format logic is mirrored. Build order: T45-T48 → GATE-3a → T49-T51 → GATE-3 → T52-T53.

### Task T45: ACORD gold-chunk labels for reachability scoring

**Description:** Produce the gold-chunk label set the reachability metrics score against. For ACORD this
is cheap and deliberately so: clauses are pre-segmented, so a clause is a chunk, and the graded
query-clause pairs (qrels) at the existing grade floor already name the gold chunks. The work is mapping
qrel corpus-ids onto `chunk_id`s for the ingested clauses, recording gold as a set per query (ACORD
questions routinely have several relevant clauses), and exposing both any-gold and all-gold readings. No
span projection is needed, which is why ACORD is the first slice rather than a span-annotated corpus.

**RAC-45:**
- [ ] Every ACORD test-split query maps to its gold `chunk_id` set, derived from qrels at the same grade
  floor `eval/acord.py` uses (`RELEVANCE_FLOOR = 2`), with unmapped qrel corpus-ids reported, not dropped.
- [ ] Gold is a set per query; the harness exposes any-gold and all-gold readings separately.
- [ ] The label set is regenerable by command and keyed to the ingested corpus, so a re-ingest
  regenerates rather than invalidates it.
- [ ] Coverage is reported against the T33 baseline population, so reachability and recall@50 are
  computed over the same queries.
- [ ] A debug split is declared and recorded: the queries reserved for single-query iteration (T51) are
  named up front and held out of the headline GATE-3 number.

**Verification:** `uv run pytest eval/test_okf_gold.py` — 7 passed. Rebuild: `uv run python -m eval.okf_gold`
→ 57 test queries, 475 distinct gold chunks, **0 unmapped**, 475 induced category labels / 9 categories,
debug split 7/50. Full suite 441 passed + 27 skipped.
**Dependencies:** T33. **Scope:** S. **Status:** done.
**Files:** `eval/okf_gold.py`, `eval/test_okf_gold.py`, `data/eval/okf_gold.json` (gitignored artifact).
**Note:** Corpus-neutral shape, ACORD-cheap instance. Also emit the **qrels-induced silver category labels**
here as a side artifact: read `queries.jsonl` `metadata.category` (the loader ignores it today) and induce
a category on each gold clause from the query it is relevant to (test split: 57 queries → 475 gold clauses,
only 1 multi-label). These feed T46's category-quality report and T48's control. On a span-annotated corpus
(for example CUAD) gold must instead be anchored to source-document coordinates and projected onto
`chunk_id`s per compile; that is a separate task, not needed here.

### Task T46: OKF bundle compile (chunk-only, category tree + LLM one-line descriptions via a cheap model)

**Description:** Compile the ingested clauses into an OKF v0.1 conformant bundle. **Not** a re-chunk:
clauses are pre-segmented, `chunk_id`s are fixed (FR-S.2), bodies come from the chunk-text sidecar (T40)
which already guarantees the text matches the identifier's content hash. The work is signpost
construction: the **manufactured category signpost** (each clause classified into a corpus-appropriate
label set — for ACORD, its own 9 attorney categories — via the model-profile seam, **not** bound to
`graph_extraction`'s 41 CUAD ontology, driving the directory tree root→category→clause and the `tags`),
**LLM-generated one-line descriptions** (ACORD's stored summary IS the full clause text, not a one-liner
— verified at build — so a discriminating description is generated in the SAME gated enrichment call as the
category, via the cheap `OKF_ENRICHMENT` model role, ADR-0023, so it is not a second model pass), non-empty
`type` frontmatter, source-document fields, `index.md` per directory, and a conformance lint. The
bundle root is stamped with `okf_version` and the compile-recipe version. Cross-links are **not** written
here (T49, behind GATE-3a), so the first reachability read (T47) measures the hierarchy, tag, category,
and description channels and treats links as a later lever.

**RAC-46:**
- [x] Every ingested clause has a bundle file whose body is byte-faithful to the sidecar text for its
  `chunk_id`, and whose frontmatter carries a non-empty `type`. No chunk identifier changes.
- [x] The category signpost is produced by a corpus-appropriate classifier (**not** bound to
  `graph_extraction`'s 41 CUAD ontology); coverage and confidence are reported against the qrels-induced
  labels (T45), and clauses with no confident category land in a recorded fallback subtree, not dropped.
- [x] `index.md` files exist at every level, each entry carrying an LLM-generated one-line description
  (a real discriminator, not a restated title). log.md is deferred to T52 (incremental-recompile lifecycle).
- [x] The conformance linter passes and reports orphan rate, description coverage, and broken-link ratio
  as numbers.
- [x] Bundle root records `okf_version` and the compile-recipe version.
- [x] Content-hash gated (recompiling an unchanged corpus does effectively no work); the bundle is
  written under gitignored `data/`, never committed.
- [x] Selective recompile: a single subtree recompiles under a modified recipe without a full rebuild
  (T51 depends on this).

**Verification:** `uv run pytest tests/capabilities/test_okf_compile.py` — 10 passed. Compile:
`uv run python -m rag_wright.okf.compile` → **3,931 clauses, 3,491 categorized** into ACORD's 9 (440
`_uncategorized` = honest "None of these"; 1 deterministic fallback); lint **passes=True, orphan 0.000,
description_coverage 1.000, broken_link 0.000**. Full suite 451 passed + 27 skipped.
**Dependencies:** T40, T45. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/okf/{__init__,document,enrich,compile,lint}.py`, `capabilities/registry.py`
(+slug `okf_compile`), `models/profiles.py` (+role `OKF_ENRICHMENT`, ADR-0023),
`tests/capabilities/test_okf_compile.py`; bundle + enrichment cache under gitignored `data/acord/okf/`.
**ARD category:** canonical slug `okf_compile`, internal registry entry, **no ARD manifest** (category 3,
foundation derivation, same as `ontology_registry_derivation`). Open question 14 applies: this builds the
chunk-only bundle; a concept layer above the chunks is a lossy synthesis, deliberately not built yet.
**Diagnostic (pre-T45, 2026-07-22 — resolved; ADR-0022 addendum):** the assumed `graph_extraction` fit
does NOT hold — its 41 CUAD categories cover only **67%** of ACORD's gold-clause mass (Indemnification
121 + Affirmative Covenants 36 = 157/475 have no CUAD home). A **direct classifier into ACORD's own 9
categories agrees with the qrels-induced labels 91.6%** (deepseek-v4-pro, 475/475, per-category:
Indemnification 100%, only Restrictive Covenants weak at 62%). Decision: **decouple the signpost from
graph_extraction's ontology** (above). **Skew caveat:** 318/475 gold clauses (67%) sit in 2 categories, so
category is a strong *bucketer* but a coarse *localizer* — within-branch localization (descriptions, links)
carries the queries that dominate the eval. The graph_extraction ontology gap itself is logged as **T54**
(deferred, ask-first); it is a graph-quality issue for non-CUAD corpora, separate from this experiment.

### Task T47: Reachability analyzer and signpost ablation

**Description:** The deterministic, model-free analyzer that computes whether each gold chunk is
discoverable from the bundle root through signposts within the depth bound and frontier budget, plus the
ablation runner that recomputes reachability with one channel removed at a time. It separates the
compile-side ceiling from traversal-side realized recall, so a disappointing traversal number is
diagnosable. It runs before the traversal exists and is the per-corpus reachability spike (GATE-3a): a
ceiling below the grounded bar redirects the experiment cheaply. Evaluation software: no runtime
capability, no registration, no manifest.

**RAC-47:**
- [ ] Reachability is computed per gold chunk, model-free and reproducible, reporting connectivity
  reachability (present and connected at all) and signpost reachability within bounds separately.
- [ ] Reported at both any-gold and all-gold readings, over the same query population as the T33 baseline.
- [ ] Per successful hop, the signpost channel that carried it is recorded, so the ablation has
  something to attribute to.
- [ ] The ablation runner recomputes reachability with each channel removed (tags, index descriptions,
  category tree, and — once T49 lands — cross-links), reporting the cost of each channel.
- [ ] Signpost what-if: reachability for a single chunk is recomputable under a hypothetical frontmatter
  or description change, without recompiling the bundle (T51 depends on this).
- [ ] Results stamped with the compile-recipe version.

**Verification:** `uv run pytest eval/test_reachability.py` — 9 passed. Run: `uv run python -m eval.reachability`
/ `uv run python -m eval.ablation`. **Real ceiling (tightened lexical proxy, budget 50):** connectivity 1.000,
**any-gold 0.930, per-gold recall ceiling 0.776, all-gold 0.491**; ablation: description carries it (cost
0.351), category_tree −0.018, tags 0.018, frontier 0.000. Full suite 460 passed + 27 skipped.
**Dependencies:** T46. **Scope:** M. **Status:** done.
**Files:** `eval/reachability.py`, `eval/ablation.py`, `eval/test_reachability.py`.
**DECISION (ADR-0022 addendum 2):** the ceiling (recall-relevant 0.776) clears the bar (0.667) and doubles the
baseline (0.379) with NO model call, so **viability is settled — FR-K can work for ACORD.** This is no longer
go/no-go. T50 is mandated to **realize** the ceiling by refinement+iteration; a shortfall is a policy/agent
failure to diagnose+fix (T51), not a verdict against the approach. Description quality is the lever (it carries
the ceiling); the category tree buys navigation efficiency, not reach.
**Note:** Open question 11 (within-branch full-text as a channel) and 13 (any-gold vs all-gold headline) — the
headline is the per-gold recall ceiling (0.776); full-text-as-channel deferred (descriptions already carry it).

### Task T48: Category label-retrieval control arm

**Description:** Build the T41 backlog item and run it as the **control arm for GATE-3a and GATE-3**, not
an independent experiment. It matches on the manufactured clause-category label directly (T46, from
`graph_extraction`) instead of on an embedding — the cheapest mechanism that bypasses the query-
representation gap. Its purpose is to establish how much of the lift comes from coarse label matching
alone, so OKF traversal is measured against a fair alternative, not against the 0.379 two-leg baseline it
will trivially differ from. Because control and bundle tree share the same manufactured labels, they
share one point of failure (category-tagging quality); that is stated, and category quality is reported
by T46 so the control's ceiling is known.

**RAC-48:**
- [ ] Category label-retrieval is wired into the candidate pool and measured on the same ACORD test
  split, at the same evidence-feed cap, as every other arm.
- [ ] Recall at the gate k and the diagnostic nDCG@10 are reported alongside the two-leg baseline.
- [ ] Cost is reported on the same axes as T50 (latency above all), so the comparison is
  quality-per-cost.

**Verification:** `uv run pytest eval/test_category_retrieval.py` — 4 passed. Run: `uv run python -m
eval.category_retrieval`. **Real result (57 test queries, 0 model calls):** **recall@50 0.134**, recall@10
0.019, nDCG@10 0.022, **containment (gold-in-bucket) 0.895**. vs two-leg baseline 0.379, reachability ceiling
0.776. Full suite 464 passed + 27 skipped.
**Dependencies:** T46. **Scope:** M. **Status:** done.
**Files:** `eval/category_retrieval.py`, `eval/test_category_retrieval.py`.
**GATE-3 read (cost/value):** the category label is RIGHT (containment 0.895 ≈ T47 depth-1) but CANNOT localize
within large buckets (LoL 489, IP 817) with no ranking → recall@50 0.134, below even the embedding baseline
0.379 and far below the ceiling 0.776. The whole containment→recall gap is within-bucket localization, exactly
what OKF's description-based traversal provides. So the cheap control does NOT make OKF redundant: T50's value
(description localization) is justified. Honest caveat: control has no intra-category ranking by design (label,
not embedding), so 0.134 is label-only; 0.895 is its ranking-independent ceiling.
**Note:** Running this as the control keeps the gates honest. If coarse label matching captures most of
the lift, the OKF compile is not justified by the numbers — a valid, cheap early result. The control uses
the same corpus-appropriate direct classifier as T46 (not graph_extraction) against the T45 induced labels.
The pre-T45 diagnostic (ADR-0022) previews its ceiling: classification agreement is high (91.6%), so
bucketing is strong, but recall is bounded by within-branch localization given the 2-category skew — the
control likely lands in the middle, which is exactly the tension GATE-3/GATE-3a adjudicate.

### GATE-3a: Reachability ceiling — **RESOLVED (2026-07-22): PROCEED. Viability settled, not a kill-switch.**

**Outcome (recorded):** T47 measured the ceiling at **any-gold 0.930 / per-gold recall 0.776 / all-gold 0.491**,
connectivity 1.000, model-free. The recall-relevant ceiling (0.776) clears the grounded bar (0.667) and doubles
the two-leg baseline (0.379). So the compiled bundle **has the information** to reach the gold — the compile is
NOT the bottleneck for ACORD. **GATE-3a is no longer a go/no-go**: viability is proven (ADR-0022 addendum 2).
The reframe: T50 is **mandated to realize this ceiling** by refinement+iteration; a shortfall is a policy/agent
failure to diagnose+fix (T51), not a redirect-or-shelve verdict.

**Still live (not viability, but cost/value):** T48 (category-label control) — is OKF traversal's complexity
justified against the cheap mechanism? The ablation says **descriptions carry the ceiling** (cost 0.351; category
tree −0.018), so recipe refinement (T51) targets description quality, and the category tree is kept for
navigation efficiency, not reach.

**Per-corpus reuse:** for a NEW corpus, GATE-3a stays the reusable model-free spike (run T45-T48, read the
ceiling) — there it can still say "shelve" if that corpus's ceiling is low. For ACORD it said proceed.

### Task T49: Cross-linking from measured clause-relation structure

**Description:** Write the link graph into the bundle, standard markdown links absolute from the bundle
root (not double-bracket wikilinks). Edges are derived from structure T41 measured (relevant-set cluster
tightness 0.686, missed-to-anchor cosine 0.706; clause category as a coarser relation), not guessed. The
T41 discipline: measurements justify the edges existing, they do **not** predict that following them helps
(T41's finding was reachability without rankability). Links are navigation affordances for a model reading
signposts, not similarity expansion — the thing GATE-3 tests. When links land, reachability (T47) is
re-measured with the link channel and the ablation shows its marginal contribution.

**RAC-49:**
- [ ] Links are standard markdown, absolute from the bundle root, and resolve (broken-link ratio reported
  by the T46 linter).
- [ ] Edge construction is derived from measured structure, the derivation recorded, and edge density
  bounded so the link graph does not degenerate into near-complete connectivity.
- [ ] Link traversal degrades on a dangling link rather than faulting (OKF broken-link tolerance).
- [ ] Cross-links are regenerable independently of the bundle compile, so link recipes can be ablated
  without a full recompile.

**Verification:** `uv run pytest tests/capabilities/test_okf_links.py` — 6 passed. Apply: `uv run python -m
rag_wright.okf.links`; re-measure: `uv run python -m eval.reachability`. **Real result (Option-1 embedding-free,
shared-distinctive-term edges):** 20,033 edges over 3,931 clauses, mean degree 5.10 (cap 8, df>100 skipped), 952
isolated, broken-link 0.0000. **Cross-links RAISE the reachability ceiling: per-gold 0.776→0.845 (+0.069),
all-gold 0.491→0.614 (+0.123), any-gold 0.930→0.947** (carry 43 gold instances descriptions/category missed).
**→ Option-2 embedding-kNN NOT needed** (embedding-free links already lift it; kept as an unused fallback). Full
suite 470 passed + 27 skipped.
**Dependencies:** T46, T41. **Scope:** M. **Status:** done.
**Files:** `src/rag_wright/okf/links.py`, `tests/capabilities/test_okf_links.py`; `okf/lint.py` extended to scan
concept-body links; `eval/reachability.py` gained the `cross_links` channel + `load_cross_links`.
**Bugs the live run caught:** the linter only scanned `index.md` links (missed all cross-links — a RAC-49 hole);
`_clause_body` newline inconsistency broke idempotent re-linking. Both fixed.

### Task T50: `okf_navigate` traversal capability

**MANDATE (post-GATE-3a, ADR-0022 addendum 2):** T47+T49 proved the bundle can reach a per-gold recall ceiling of
**0.845** (T47 descriptions 0.776 + T49 cross-links; > bar 0.667, ~2.2x baseline 0.379), model-free. So T50 is NOT
testing whether navigation works — it is **realizing a known-achievable ceiling**. The success target is the
reachable set: for each query, the traversal should reach the gold clauses the analyzer marked reachable. Read
every run against the 2x2 — reachable-and-reached (good) vs **reachable-not-reached (the policy gap to close via
T51)**. If realization lags the ceiling, iterate the recipe/prompt/params (T51); do not conclude the approach
fails. Description quality + cross-links are the proven levers.

**Description:** Build the traversal: one interpreter node that reads the bundle root `index.md`, filters
candidate subtrees by frontmatter predicate and index description, expands the frontier through links and
index entries, dispatches one reader sub-agent per surviving body, deduplicates against a visited set, and
stops on convergence or the depth and frontier bounds, returning a `chunk_id` shortlist plus the trace. It
computes no query-to-chunk similarity anywhere. Structurally this is the RLM dynamic-sub-agent machinery
(T15/T36/T37) applied to a bundle: PTC does the deterministic sift (enumerate, read index, parse
frontmatter, filter, resolve link) so filtering costs no model call; loop-until-done with a `seen` set
drives frontier expansion so completeness does not depend on a fixed k; fan-out-and-synthesize reads
bodies one per sub-agent so per-call context stays bounded; recursion handles depth. Run parameters (model
via the T11 model-profile seam, depth and frontier bounds, PTC allowlist, repeat count) are ordinary
configuration recorded with each result — no separate harness-profile seam.

**RAC-50:**
- [ ] Returns a `chunk_id` shortlist and a full trace (nodes visited, order, prune decisions); no code
  path computes a query-to-chunk embedding similarity.
- [ ] Filtering by frontmatter predicate happens before any body is read, verifiable in the trace as
  bodies-read being a small fraction of candidates-considered.
- [ ] One interpreter session per traversal; sub-agents fan out with parallel dispatch inside a single
  interpreter (ADR-0020, KI-1; the T35 per-process serialization floor applies).
- [ ] Per-call token usage stays bounded as round count grows, and each reader sub-agent receives its own
  body and the query by enforcement, not orchestrator model choice (the T42 lesson).
- [ ] Cost telemetry per query: serial round count (primary latency proxy), total dispatches, peak
  frontier, total and peak-per-call tokens, wall-clock latency, max depth.
- [ ] Variance characterized: k repeated runs per query under fixed run parameters, Reached reported as
  reached-always / reached-sometimes / never-reached plus the cost spread. Exact replay is not assumed
  (fresh navigation code each run); the spread is the run-variance floor any tuning improvement is judged
  against.
- [ ] All run parameters recorded with every result.
- [ ] Registered under canonical slug `okf_navigate` with an ARD manifest whose representative queries are
  authored (category 1), loading under `RegistryStore(root)` with no `RegistryLoadError`.

**Verification:** `uv run pytest tests/capabilities/test_okf_navigate.py`, plus an opt-in live model test
(`-m model`) for traversal quality (branch-choice is model-dependent; stubbed responders miss it).
**Dependencies:** T46, T47, T49, T35. **Scope:** L. **Status:** todo (behind GATE-3a).
**Files:** `src/rag_wright/capabilities/okf_navigate.py`, `capabilities/registry.py`,
`capabilities/manifests.py`, `tests/capabilities/test_okf_navigate.py`.
**Note:** GraphWright needs nothing new: the node is `needs_interpreter` + a PTC allowlist + configured
sub-agents + the optional `rlm` marker, all of which exist. Do **not** split the sift and the read into
two interpreter nodes: the working set (frontier, visited set, shortlist) lives in interpreter variables;
splitting forces serialize-and-rehydrate across a node boundary and loses the convergence check.

### Task T51: Single-query trace-and-iterate harness

**Description:** The tuning loop. Take one failing query, see exactly where and why the traversal went
wrong, change a recipe or run parameter, re-run until it works, then confirm the change generalizes rather
than fitting the query. This is the instrument that **produces** the data-specific signpost recipes the
design assumes. The loop tunes the procedure (compile recipe, prompts and skills, run parameters, tool
surface), never the data — a hand-edited bundle file makes the compile-recipe stamp a lie and evaporates
on the next compile, so if a fix cannot be expressed as a recipe or parameter change, it is not a fix.

**RAC-51:**
- [ ] One command takes a query id and dumps every artifact: reachability verdict, traversal trace, the
  2x2 cell, cost telemetry.
- [ ] Reachability runs first and model-free — a gold chunk that was never reachable is reported before
  any model call is spent.
- [ ] Divergence point: the trace is joined against gold to report the frontier step where a gold chunk's
  ancestor was available and not expanded, with the verbatim signpost text the model saw (index entry,
  description, the predicate that rejected it). Distinct from a list of prune decisions.
- [ ] Fast iteration: a signpost what-if (T47) and a selective subtree recompile (T46) both run without a
  full rebuild.
- [ ] Persist and diff: traces persisted with compile-recipe and run-parameter versions, two runs
  diffable at the trace level.
- [ ] Compare distributions, not runs: a change is evaluated as k runs before against k after (exact
  replay unavailable, so a single before-and-after pair is not evidence).
- [ ] Variance floor: an improvement on a single query is reported against the k-run spread from T50.
- [ ] Regression guard: any recipe or parameter change triggers a population re-run reporting per-query
  deltas in both directions.
- [ ] Held-out reporting: results reported separately for the declared debug split (T45) and the held-out
  remainder, so the generalization claim is demonstrated, not asserted.

**Verification:** `uv run pytest eval/test_trace_iterate.py`. Run:
`uv run python -m eval.trace_iterate --query <id>`.
**Dependencies:** T47, T50. **Scope:** L. **Status:** todo (behind GATE-3a).
**Files:** `eval/trace_iterate.py`, `eval/trace_diff.py`, `eval/test_trace_iterate.py`.
**Note:** The held-out split turns a reasonable generalization expectation into evidence GATE-3 can score.
The variance floor matters because navigation code is model-emitted at run time, so the tuning signal is
noisier than for a deterministic pipeline.

### GATE-3: FR-K — realization vs the proven ceiling, and cost vs the control (NOT graduate-or-remove-viability)

**Reframed (ADR-0022 addendum 2):** viability was settled at GATE-3a (per-gold recall ceiling 0.776, model-free).
GATE-3's **"remove because it can't reach" branch is off the table for ACORD.** GATE-3 now asks: (a) how close did
T50 realization get to the 0.776 ceiling (and is the residual reachable-not-reached gap closed or diagnosed via
T51), and (b) is OKF traversal's cost justified against the T48 category-label control. Outcomes narrow to
graduate (realized near the ceiling, worth its cost) or keep-narrow (works but the cheap control captures most of
the lift); "remove" only returns if realization is fundamentally, unfixably below the proven ceiling — which the
mandate treats as a bug to fix, not an expected outcome.

**Decision:** whether the embedding-free OKF path graduates from experimental, stays a narrow capability,
or is removed.

**Inputs:** T47 reachability and ablation, T50 Reached and cost telemetry, T51 held-out results, T48
control arm, all against the T33 ACORD baseline (two-leg recall@50 0.379, grounded bar 0.667, raw-hybrid
recall@200 ceiling 0.65) at a single, explicitly bound evidence-feed cap.

**Reading rules (set before the run):**
- Compare arms at the **same** gate k and the same `fuse(..., cap=k)` binding.
- Report the **2x2**: reachable-and-reached; reachable-not-reached (policy failure, fix traversal/params);
  not-reachable-not-reached (scaffolding failure, fix the compile recipe); not-reachable-but-reached (the
  reachability model under-counts a channel, fix the model). A single recall number without this split is
  not interpretable.
- Low reachability is not a traversal verdict.
- Score the held-out split, debug split reported separately.
- Judge against T48, not only against the baseline.
- Report the winning compile-recipe and run-parameter versions with the number.
- Report cost alongside quality (serial round count and latency distributions first-class), since FR-K is a
  capability, not a default.
- Strategy memory (T53) is off for the graded run; memory-on is measured separately, after.
- Do not lower the grounded bar (E / 0.75 with E = 0.5).

**Outcomes:** graduate (FR-K leaves experimental, a routing question opens as its own task), narrow (keep
as a registered capability for a bounded question class, no default change), or remove (excise the FR-K
block; the reachability instrument, the trace harness, and the ACORD gold labels are kept regardless).

### Task T52: Bundle lifecycle

**Description:** Incremental recompile, update, and delete for the bundle, sharing the single content-hash
gate that already couples the index and the chunk-text sidecar (T40), so a bundle cannot drift from the
store. Deleting a chunk removes its file and repairs or records inbound links, since a stale link silently
lowers reachability and reports itself nowhere. Gated behind GATE-3: lifecycle work on a bundle that has
not earned its place is wasted.

**RAC-52:**
- [ ] Bundle write is driven by the same content-hash gate as the index and sidecar writes, so an
  unchanged document skips all three and a changed chunk writes all three.
- [ ] A completeness guard rejects a bundle state where an indexed chunk has no bundle file, before any
  write, mirroring the T40 guard.
- [ ] Delete-by-`source_doc_id` removes bundle files and repairs or records inbound links; a post-delete
  reachability regression check runs against the golden subset.
- [ ] Recompiling an unchanged corpus does effectively no work.

**Dependencies:** GATE-3, T34. **Scope:** M. **Status:** todo (post-GATE-3).
**Files:** `src/rag_wright/okf/lifecycle.py`, `tests/capabilities/test_okf_lifecycle.py`.
**Note:** Couples to T34 (document update/upsert), the existing lifecycle gap. If T34 lands first, the
bundle is a third consumer of the same lifecycle seam rather than a parallel path.

### Task T53: Strategy memory

**Description:** Store what worked, and reuse it. When a traversal solves a query, record the originating
query, the navigation strategy, and the outcome, so a later query of similar shape retrieves a known-good
strategy instead of generating one. The second answer to run-to-run variance, and a better one: it does
not make sampling deterministic, it removes the sampling step for queries that resemble solved ones.
Placed after GATE-3 deliberately: it makes the evaluation stateful, and recognizing a strategy worth
keeping presupposes knowing what a good one looks like (T51's output).

**What is stored.** The primary artifact is a **parameterized strategy** (for queries of this shape: filter
frontmatter by these tags, expand along these link kinds, at this depth, read bodies at these leaves), with
the raw emitted code attached as a reference artifact, not the thing retrieved. The parameterized form is
the transferable unit; raw code hardcodes paths and breaks on the first compile-recipe change.

**Admission, two regimes.** Offline, gold is available, so admission means verified (a case enters only if
the traversal retrieved the gold chunks). In production, gold is absent, so admission is judge-approved or
downstream-signal-approved. Bootstrap: build memory offline from gold-verified runs, ship it seeded, let
production accumulate under judge approval.

**RAC-53:**
- [ ] A solved query records a parameterized strategy plus the raw emitted code as an attached artifact,
  stamped with the compile-recipe and run-parameter versions.
- [ ] Case retrieval does not use query embeddings — cases are keyed on structural features (types and
  tags involved, predicates that fired, subtrees that proved productive). Indexing by query embedding would
  reintroduce the query-representation gap this path exists to avoid, one layer up.
- [ ] Offline admission is gold-verified; production admission is judge-approved or
  downstream-signal-approved, and the two paths are distinguishable in the stored case.
- [ ] Staleness: a case whose compile-recipe version no longer matches the live bundle is invalidated, not
  silently applied.
- [ ] Exploration path: a configured fraction of runs re-derives from scratch, so a subtly wrong cached
  strategy cannot lock the system into being reliably wrong.
- [ ] Eval-state discipline: memory is cold- or warm-started explicitly; cold and warm runs reported
  separately, a warm run records query order.
- [ ] No cross-split leakage: cases from the declared debug split (T45) do not populate memory serving
  held-out queries.
- [ ] Variance reduction is measured: k runs memory-on vs off, reporting the change in the reached-always /
  reached-sometimes / never-reached distribution and the cost spread.

**Dependencies:** GATE-3, T50, T51. **Scope:** L. **Status:** todo (post-GATE-3).
**Files:** `src/rag_wright/okf/strategy_memory.py`, `tests/capabilities/test_strategy_memory.py`.
**Note:** The natural implementation stores cases as an OKF bundle navigated with the same progressive
disclosure the corpus uses, so memory and corpus share one mechanism. Scoped to traversal strategies only;
does not settle the general agent-memory question.

### Task T54: graph_extraction ontology extension (deferred, ask-first — NOT FR-K)

**Description:** Spun off from the pre-T45 diagnostic (ADR-0022). `ClauseCategory` is a hardcoded 41-member
CUAD enum that `ClauseFact` strictly validates against, so `graph_extraction` (and therefore `graph_query`)
cannot represent clause types CUAD lacks — for ACORD, Indemnification (25% of the eval's gold mass) and
Affirmative Covenants have no home. This is a **graph-quality** issue for any non-CUAD corpus, separate from
the OKF experiment (which decouples its signpost classifier and needs none of this). Not scheduled; recorded
so the gap is visible. The viable extension shapes (ADR-0022 addendum): **T8-derived ontology** (SPEC §17 /
assumption 3, the aligned path), **static enum extension** (cheapest, pollutes the CUAD contract), or an
**open-set proposal/verification seam** (the FR-C.7 / T23b pattern). A **crosswalk/hierarchy layer is ruled
out** — it can coarsen an existing taxonomy but cannot invent missing coverage. Any option is a data-model
change (ask-first, load-bearing) and must preserve strict-reject of the genuinely-unknown.

**Dependencies:** T8, T23. **Scope:** M-L (T8-derived) / S (static). **Status:** deferred (ask-first).
**Files (if taken up):** `src/rag_wright/contracts/ontology.py`, `src/rag_wright/ontology/derive.py`,
their tests. **Note:** decide the shape with us before touching the ontology; do not extend it as a side
effect of any OKF task.

---

## Requirements coverage map

Every spec requirement traces to a task (or is explicitly out of scope / compiler work).

| Requirement | Task(s) |
|---|---|
| FR-S.1 one store | T3, T13, T25 |
| FR-S.2 `chunk_id` | T1 |
| FR-S.3 `entity_id` | T1, T8 |
| FR-S.4 provenance/confidence | T2 |
| FR-S.5 query-skill seam / MCP | T6, T13, T31 |
| FR-C.1 parsing | T16 |
| FR-C.2 embedding | T19 |
| FR-C.3 hybrid search | T21 |
| FR-C.4 reranking | T22 |
| FR-C.5 graph query | T26 |
| FR-C.6 graph extraction | T5, T23 |
| FR-C.7 entity resolution (canonicalization + linking) | T8, T23b, T24 |
| FR-C.8 ontology/registry derivation | T4, T8 |
| FR-C.9 reasoning/generation/vision-to-text | T29 |
| FR-C.10 RLM skill | T15 |
| FR-I.1 RLM chunking | T17 |
| FR-I.2 small-to-large escalation | T18 (GATE-1 conditional) |
| FR-I.3 chunk record | T3, T19, T20 |
| FR-I.4 graph extraction over parsed docs | T23, T25 |
| FR-I.5 incremental / idempotent / dead-letter | T20, T25 |
| FR-I.6 (three parts) | **Model-tiering:** T11, T18. **CPU-GPU decoupling / pooled inference / backpressure:** built-in acceptance criterion on T19 (embedding) and T23 (extraction). **Two run modes (bulk vs background):** out of scope — orchestration/deployment, not a capability |
| FR-Q.1 hybrid search | T21 |
| FR-Q.2 rerank cutoff | T22 |
| FR-Q.3 graph answer as evidence | T26 |
| FR-Q.4 fusion union/dedup | T27 |
| FR-Q.5 RLM synthesis | T28 |
| FR-Q.6 grounded/cited/abstains | T29 |
| §12 golden eval + archetypes | T7, T9, T10, GATE-1, GATE-2, T32 |
| §8 graph relational/multi-hop | T10, T26, T32 |
| FR-K.1, FR-K.2, FR-K.4 (OKF bundle compile) | T46 |
| FR-K.3 (cross-linking) | T49 |
| FR-K.5, FR-K.6 (navigation primitives + traversal) | T50 |
| FR-K.7 (bundle lifecycle) | T52 |
| FR-K.8 (reachability spike) | T47, T51 |
| FR-K.9 (strategy memory) | T53 |
| §13 Phase 5 / §15 (FR-K gates) | GATE-3a, GATE-3 |
| §3.2 extras | Out of scope / ask-first (not scheduled) |
| §3.2 durable memory backend | Out of scope (engine-side integration; no FR-S.6, no task) |

---

## Risks carried from plan.md section 3

De-risked early by foundation tests: risk 1 (ArcadeDB recall) by T14/GATE-2; risk 3 (structured
output on open models) by T12 on DeepSeek V4 Pro. Risk 2 (arcadedb-python v0.x) is mitigated by
grounding every call (T13, T21, T25, T26). Risk 4 (chunking determinism) is tested in T17. Risk 5 (entity-resolution fragmentation) is addressed at T23b (canonicalization) and measured at T23b and T24. Risk 7 (identifier schemes) fixed at T1.
Risk 8 (summary-miss) is designed into T19 and tested there and at T32. Risk 9
(citation/abstention) is enforced at T29. Risk 10 (OpenIE undecided) is deferred behind the T5
extractor seam.
