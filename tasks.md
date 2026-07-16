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

---

## Last approved / next up

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
| T33 | ACORD content-query retrieval eval (extends T9; ask-first) | 4 Foundations | §12 | todo | T21, T22 |
| T34 | Document update/upsert: on doc change, delete a document's chunks + graph nodes + index entries, then re-chunk and re-insert | 5 Integrate | FR-I.5 | todo (finding) | T17, T20, T25 |
| T35 | Concurrent-batch ingestion throughput design (KI-1 correctness floor already always-on) | 5 Integrate | OQ8, ADR-0020 | todo (throughput design; floor landed) | T17, T28 |
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
**Status:** todo (ask-first gate). **Dep:** T21, T22. License + corpus provenance verified (2026-07-12).

### Task T34: Document update/upsert path (finding, logged during T17) — later

**Description:** Confirmed during the T17 rebuild (2026-07-15): **no document-level update/upsert path
exists**. The store has `upsert_chunk` (per-chunk) and `write_document` (a document's chunks, content-hash
gated) but **no delete-by-document**. On a document *change* the content hash changes → new `chunk_id`s →
`write_document` upserts the new chunks while the **old chunks orphan** (and their graph nodes + index
entries with them). This delete-and-re-chunk route is the only way a document is ever chunked more than
once, and it is what makes the "chunk once, persist, never recompute" ingestion lifecycle complete.

**Scope (when built, not now):** on a changed document, delete all existing chunks for that `source_doc_id`
(and their graph nodes and hybrid-index entries), then re-chunk and re-insert from scratch. Touches
chunking (T17), the chunk write/store (T20, ArcadeDB `store/`), and the graph layer (T25). Needs a
delete-by-`source_doc_id` on the store seam + graph, wired into a document-update entry point.

**Status:** todo (finding — not a T17 blocker; makes the ingestion lifecycle complete). **Dep:** T17, T20, T25.

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
