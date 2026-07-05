# tasks.md: RAG_Wright task ledger

Phase 2 output. The persistent, cross-session task ledger and shared memory of progress. Derived
from `plan.md` (Phase 1) and `SPEC.md` v0.1, honoring ADR-0001 (stack) and ADR-0002 (corpus).

This is the **capability half** only. Each task builds and registers one FR-C / FR-I / FR-Q
capability, or a foundation seam, as ordinary tested software. Registration is twofold and is part
of every capability's definition of done: internal registration (T6, which the Model Context
Protocol surface exposes) and Autonomous Resource Directory (ARD) registration (the manifest the
GraphWright compiler discovers and binds against); see "ARD registration" below. The ingestion and
query **graphs** are compiled separately from the Orchestration Spec by the GraphWright compiler
and are not built here. Any task that looks like "wire the pipeline into a graph" is compiler work,
not this repo's.

> Conventions: acronyms expanded on first use, no em dashes, plain phrasing.

---

## Last approved / next up

- **Last approved:** **T9** (§12, RAC-9) — golden eval harness (recall@k per archetype, legs
  separate) + CUAD-annotation golden set (2,032 subset questions across exact/lexical, semantic,
  clause-finding), pinned + reproducible. Archetype map is a testable hypothesis. (T8 `c1f43aa`.)
- **Next up:** Phase 4 (Foundations) — **T10** (EDGAR-derived relational + multi-hop question
  construction, §12/§8, RAC-10). Builds the RELATIONAL archetype from the EDGAR party-and-entity
  graph, with **human-verified** name→CIK matches (from T7's UNVERIFIED proposals) — the only path
  to ground truth. Gated on T7, T9.
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

ARD registration is the Autonomous Resource Directory manifest that lets the GraphWright compiler
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

Definition of done. Every task that builds a discoverable, bound capability carries the final
acceptance bullet appended below and is `done` only when its ARD manifest is authored
(representative queries filled) and loads under `RegistryStore(root)` with no `RegistryLoadError`.
This covers the RLM skill (T15) and each FR-C / FR-I / FR-Q capability that is its own bound graph
node (T16, T17, T19, T20, T21, T22, T23, T24, T25, T26, T27, T28, T29). It does not cover the
contracts, the seams (T11, T13), the foundation tests, the corpus and eval tasks, the T18
escalation path (it extends the T17 capability, not a new one), the T30 caching optimization, or
the T31 MCP surface. Stated once here as the source of its meaning, repeated per task so the
working loop enforces it.

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
| T10 | EDGAR-derived relational + multi-hop question construction | 4 Foundations | §12, §8 | todo | T7, T9 |
| T11 | Model-profile seam (DeepSeek V4 Pro first for structured-under-reasoning) | 4 Foundations | assumption 2, tech stack | todo | T6 |
| T12 | A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR) | 4 Foundations | FR-C.6 dep, risk 3 | todo | T11 |
| T13 | Store seam + ArcadeDB schema and hybrid indexes | 4 Foundations | FR-S.1, FR-S.5 | todo | T3, T6 |
| T14 | A-T1 ArcadeDB `vector.fuse` hybrid foundation test | 4 Foundations | FR-C.3 dep, risk 1 | todo | T13 |
| T15 | RLM skill authoring (general method only) | 4 Foundations | FR-C.10 | todo | - |
| T16 | Parsing (Docling) | 4 Build write | FR-C.1 | todo | T3 |
| T17 | RLM chunking (deterministic, content-hash gated) | 4 Build write | FR-I.1 | todo | T15, T16, T11 |
| T18 | Small-to-large chunking escalation | 4 Build write | FR-I.2 | todo | **GATE-1**, T17 |
| T19 | Embedding (BGE-M3; concurrent + backpressure) | 4 Build write | FR-C.2, FR-I.3, FR-I.6 | todo | T16 |
| T20 | Chunk write + incremental upsert (content-hash gated) | 4 Build write | FR-I.3, FR-I.5 | todo | T13, T17, T19 |
| **GATE-1** | **RLM chunker A/B go / no-go** | 4 Build | §12, plan §2 | pending | T17, T19, T9 |
| T21 | Hybrid search (server-side RRF, metadata filters) | 4 Build read | FR-C.3, FR-Q.1 | todo | T14, T20 |
| T22 | Reranking (cross-encoder precision gate) | 4 Build read | FR-C.4, FR-Q.2 | todo | T21 |
| **GATE-2** | **Recall-bar: ArcadeDB hybrid vs LanceDB fallback** | 4 Build | FR-S.5, plan §2 | pending | T21, T22, T9, T10 |
| T23 | Graph extraction (contract + spaCy NER/dep; concurrent + backpressure) | 4 Build graph | FR-C.6, FR-I.4, FR-I.6 | todo | T5, T8, T16 |
| T24 | Entity resolution (closed-world to EDGAR CIK) | 4 Build graph | FR-C.7 | todo | T8, T23 |
| T25 | Graph storage (nodes/edges carry `chunk_id`, gated) | 4 Build graph | FR-I.4, FR-I.5 | todo | T13, T23, T24 |
| T26 | Graph query (cited `chunk_id`s, `entity_id`s, confidence) | 4 Build graph | FR-C.5, FR-Q.3 | todo | T25 |
| T27 | Fusion (union/dedup on `chunk_id`, capped) | 4 Build graph | FR-Q.4 | todo | T22, T26 |
| T28 | RLM synthesis (interpreter load, slice in code, sub-calls) | 4 Build RLM | FR-Q.5 | todo | T15, T27 |
| T29 | Answer generator (grounded, cited, abstains) + vision-to-text | 4 Build RLM | FR-C.9, FR-Q.6 | todo | T11, T27 |
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
exposes. The same seam also emits the capability's Autonomous Resource Directory (ARD) manifest
skeleton (see "ARD registration"), so the internal registry and the ARD manifest share the FR-C
name as their one key and stay coherent.

**RAC-6:**
- [x] A capability registers by canonical slug with its contract; lookup by slug returns it.
- [x] Registering an unknown (non-canonical, per SPEC §5) or duplicate name is rejected; unknown
  lookup raises. The name is the cross-spec join key — a canonical slug (`hybrid_search`, never
  `fr-c-3`), enforced against the mirrored slug set.
- [x] Registration emits an ARD manifest skeleton conforming to the mirrored GraphWright
  `RegistryEntry` schema (URN `urn:air:dreamai:rag_wright:<slug>`, kind, callable response bounds),
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
- [ ] Questions require traversal across the EDGAR party-and-entity graph (relational and
  genuine multi-hop, not single-document lookups), with ground-truth answer entities and the
  `entity_id`/`chunk_id` evidence path recorded.
- [ ] **The name→CIK links are human-verified, not auto-generated** (folds
  `docs/Corpus_Acquisition.md`): linking a contract's parties to CIKs is itself the entity-resolution
  problem (FR-C.7), so building the answer key by fuzzy matching and then testing fuzzy matching
  against it is circular. T7 produces mechanical, UNVERIFIED-marked proposals; **only human-verified
  matches enter the golden set here** — verification at T10 is the sole path from proposal to ground
  truth (same human-curated-fixture discipline as the engine's control_level eval).
- [ ] The set is registered as its own archetype split in the harness (T9), separate from the
  CUAD-annotation sets.
- [ ] Coverage is enough to measure the graph leg's recall and its per-source ablation loss (T32),
  not a token handful.

**Verification:** `uv run pytest eval/test_multihop_set.py`

**Dependencies:** T7, T9. **Scope:** M.
**Files:** `eval/multihop.py`, `eval/golden/relational/`, `eval/test_multihop_set.py`
**Note:** The entire reason the ArcadeDB graph layer exists is measured here. Do not fold into T9.
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
- [ ] A profile keyed by model id supplies structured-output method + optional structured-only
  extra body; the extra body applies only to the forced-structured call.
- [ ] The structured-under-reasoning default resolves to DeepSeek V4 Pro; Qwen 3.7 Plus is a
  selectable secondary; Gemma 4 class is the local-mode / general-generation default. All of this
  lives in profile config, and no provider/model-specific flag appears in any call site.
- [ ] `with_structured_output(...)` is reached only through the seam.

**Verification:** `uv run pytest tests/models/test_profile_seam.py` (unit, mocked client — no
network).

**Dependencies:** T6. **Scope:** M.
**Files:** `src/rag_wright/models/profiles.py`, `src/rag_wright/models/seam.py`,
`tests/models/test_profile_seam.py`
**Note:** Priorities live here, never in capability code (CLAUDE.md standing rule).

### Task T12: A-T2 forced-structured-output foundation test on DeepSeek V4 Pro (+ ADR)

**Description:** Prove the model-profile seam against an actual forced-schema call **on DeepSeek V4
Pro**, the model the seam now prioritizes for structured-output-under-reasoning and the model
extraction (T23) will use, the same failure surface open models hit in thinking mode (plan A-T2,
risk 3). Learn and record the working profile (method, structured-only extra body) in a dated ADR
before extraction depends on it.

**RAC-12:**
- [ ] A forced-schema call on DeepSeek V4 Pro returns a valid contract instance through the seam.
- [ ] The working profile (method + structured-only extra body) for DeepSeek V4 Pro is recorded in
  a dated ADR; Qwen 3.7 Plus is noted as the secondary profile.

**Verification:** `uv run pytest tests/foundation/test_model_seam_structured.py -m model`
(requires model access via OpenRouter; marked `model` so it is opt-in).

**Dependencies:** T11. **Scope:** S.
**Files:** `tests/foundation/test_model_seam_structured.py`, `docs/adr/0003-model-profile.md`
**Note:** Needs OpenRouter access (`.env`). Validates the profile on the model actually
prioritized. De-risks FR-C.6 before it is built.

### Task T13: Store seam + ArcadeDB schema and hybrid indexes

**Description:** Define the query-skill seam (so the store is swappable, FR-S.5) and create the
ArcadeDB schema with the dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` indexes via the
`arcadedb_python` `SyncClient` / `DatabaseDao` (FR-S.1, §16.4). Resolves the schema/index
specifics open question at this task.

**RAC-13:**
- [ ] The query-skill seam interface is defined; the ArcadeDB implementation sits behind it.
- [ ] Schema created with dense `LSM_VECTOR` (dimensions/similarity chosen for BGE-M3) and sparse
  `LSM_SPARSE_VECTOR` indexes; chunk records and graph nodes carry `chunk_id`.
- [ ] A second stub implementation can bind the same seam (proves swappability for the LanceDB
  fallback path).

**Verification:** `uv run pytest tests/store/test_arcadedb_schema.py -m store` (requires a local
ArcadeDB instance; marked `store`).

**Dependencies:** T3, T6. **Scope:** M.
**Files:** `src/rag_wright/store/seam.py`, `src/rag_wright/store/arcadedb.py`,
`tests/store/test_arcadedb_schema.py`
**Note:** Ground every `arcadedb_python` call against the framework graph first (driver is v0.x,
risk 2).

### Task T14: A-T1 ArcadeDB `vector.fuse` hybrid foundation test

**Description:** Write a few chunk records and run a `vector.fuse` Reciprocal Rank Fusion (RRF)
hybrid query end to end, confirming the sparse index and server-side fusion behave as documented
(plan A-T1, risk 1). Surfaces early whether we are heading for the LanceDB fallback.

**RAC-14:**
- [ ] A few records written; a `vector.fuse` RRF hybrid query returns a sensible fused ranking
  honoring a metadata filter.
- [ ] The result (works as documented / lean-toward-fallback) is recorded as an early signal for
  GATE-2.

**Verification:** `uv run pytest tests/foundation/test_arcadedb_hybrid.py -m store`

**Dependencies:** T13. **Scope:** S.
**Files:** `tests/foundation/test_arcadedb_hybrid.py`
**Note:** Early de-risk of the recall-bar gate; not the gate itself.

### Task T15: RLM skill authoring (general method only)

**Description:** Author the RLM SKILL.md as the general divide-and-conquer method (load a working
set into an interpreter, slice and dispatch in code, synthesize). It has no testable behavior of
its own; the RLM chunking (T17) and RLM synthesis (T28) capabilities each apply it with their own
contract and tests (FR-C.10, plan §1 note).

**RAC-15:**
- [ ] `SKILL.md` teaches the method (interpreter load, code-side slice/dispatch, synthesize) as
  authored software, not a build-tool feature.
- [ ] It defers all determinism/boundary/gating behavior to the applying capabilities.
- [ ] ARD-registered as an `agent_skill`: its manifest loads under `RegistryStore(root)` with no
  `RegistryLoadError` and is discoverable; the RLM skill is a required bound capability of both
  graphs, so its ARD presence is what the gap-analysis gate checks before either graph compiles.

**Verification:** Manual review of `SKILL.md`; no behavioral test (correctly has none).

**Dependencies:** None. **Scope:** S.
**Files:** `src/rag_wright/skills/rlm/SKILL.md`

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
- [ ] A CUAD PDF and a scanned filing both parse into the structured representation (headings,
  sections, tables; OCR text present for the scan).
- [ ] The parsed result is cached/reusable so it is parsed once.
- [ ] Registered under FR-C.1.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_parsing.py`

**Dependencies:** T3. **Scope:** M.
**Files:** `src/rag_wright/capabilities/parsing.py`, `tests/capabilities/test_parsing.py`

### Task T17: RLM chunking (deterministic, content-hash gated)

**Description:** Read the whole parsed document through an interpreter (not bounded by a context
window) using the RLM skill (T15), splitting along topic/section/chapter boundaries into
semantically coherent chunks (variable size, capped ~20,000 tokens), writing a summary per chunk,
a manifest per document, and stable `chunk_id`s. Deterministic and reliable: temperature zero or
structured output, boundary validation, and a content-hash gate so an unchanged document is not
re-chunked (FR-I.1).

**RAC-17:**
- [ ] Same document in yields identical chunk boundaries and `chunk_id`s across runs (determinism
  test, temperature zero / structured output).
- [ ] Boundary validation runs; chunks are capped at ~20,000 tokens; a summary + manifest are
  produced.
- [ ] The content-hash gate skips an unchanged document (no re-chunk).
- [ ] Registered under the RLM chunking capability.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_rlm_chunking.py`

**Dependencies:** T15, T16, T11. **Scope:** L.
**Files:** `src/rag_wright/capabilities/rlm_chunking.py`,
`tests/capabilities/test_rlm_chunking.py`
**Note:** Resolves chunking-skill internals (§16.1). Determinism is testable (risk 4).

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

### Task T19: Embedding (BGE-M3: dense over summary, sparse over full text)

**Description:** Produce dense vectors over summaries and native sparse vectors over full chunk
text, from one model (FR-C.2, FR-I.3). The dense-over-summary + sparse-over-full-text split is the
summary-miss mitigation (§12, risk 8). This is a GPU-calling capability, so it carries the FR-I.6
decoupling property. Grounded surface: `FlagEmbedding` `M3Embedder`.

**RAC-19:**
- [ ] Dense vector produced over the summary; native sparse vector produced over the full chunk
  text, from the one BGE-M3 model. **The full chunk text is read from the parse manifest (FR-I.1),
  not from the chunk record** — the record holds only the summary + vectors (T3 decision), so the
  sparse-over-full-text leg must source the text from the manifest keyed by `chunk_id`.
- [ ] Output shapes match the chunk record contract (T3) and the store index config (T13):
  dense length `BGE_M3_DENSE_DIM` (1024); sparse emitted as `dict[int, float]` (int token-id keys,
  converting BGE-M3's `Dict[str, float]` string keys).
- [ ] **FR-I.6 decoupling property:** the capability is built to be called concurrently and is
  non-blocking, its GPU calls go through a poolable inference boundary (not a hardcoded
  synchronous single-call), and it applies backpressure at that boundary, so bulk mode can
  saturate the GPU. Verified by a concurrency test, not left for later graph wiring.
- [ ] Registered under FR-C.2.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_embedding.py`

**Dependencies:** T16. **Scope:** M.
**Files:** `src/rag_wright/capabilities/embedding.py`, `tests/capabilities/test_embedding.py`
**Note:** FR-I.6 decoupling is built in here because it is cheap now, expensive to retrofit.

### Task T20: Chunk write + incremental upsert (content-hash gated)

**Description:** Write chunk records to the store, upserting by `chunk_id`; make ingestion
incremental, resumable, and idempotent with per-document and per-chunk checkpoints and a
dead-letter queue for failed documents (FR-I.3, FR-I.5). Content-hash gating means an unchanged
document does effectively no work.

**RAC-20:**
- [ ] A chunk record upserts by `chunk_id` (re-write of the same id updates, does not duplicate).
- [ ] Re-running an unchanged document does effectively no work (content-hash gate).
- [ ] A failed document lands in the dead-letter queue; a resumed run continues from checkpoints.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_chunk_write.py -m store`

**Dependencies:** T13, T17, T19. **Scope:** M.
**Files:** `src/rag_wright/capabilities/chunk_write.py`, `tests/capabilities/test_chunk_write.py`
**Note:** Capability-level idempotence and checkpoints. The bulk-vs-background run modes (FR-I.6
part 3) are deployment/orchestration, not built here.

### GATE-1: RLM chunker A/B go / no-go (branch point)

**Not a task.** A/B the RLM chunker (T17) against a simpler baseline chunker on the golden set
(T9): boundary quality + summary fidelity (§12, plan §2).
- **Beats baseline →** keep the RLM chunker; build the small-to-large escalation (T18) and the
  full apparatus.
- **Does not beat baseline →** drop T18 and the elaborate chunking apparatus; fall back to the
  simpler chunker; redirect the plan. Raise with the human before proceeding either way.

---

## Phase 4 — Build, read-side (spec Phase 1, FR-Q)

### Task T21: Hybrid search (server-side RRF, metadata filters)

**Description:** Fuse dense-over-summary and sparse-over-full-text results server-side by RRF in
ArcadeDB into one ranked candidate list, honoring metadata filters (FR-C.3, FR-Q.1). Grounded
surface: ArcadeDB `vector.fuse` (proven end to end at T14).

**RAC-21:**
- [ ] A query returns one RRF-fused ranked candidate list from the dense and sparse legs.
- [ ] Metadata filters are honored.
- [ ] recall@k per archetype is measurable on the golden set (feeds GATE-2).
- [ ] Registered under FR-C.3.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_hybrid_search.py -m store`

**Dependencies:** T14, T20. **Scope:** M.
**Files:** `src/rag_wright/capabilities/hybrid_search.py`,
`tests/capabilities/test_hybrid_search.py`

### Task T22: Reranking (cross-encoder precision gate)

**Description:** A cross-encoder in the BGE-reranker family reranks the candidate list and cuts it
to a top set before any expensive work, the precision gate before synthesis (FR-C.4, FR-Q.2).
Grounded surface: `FlagEmbedding` `FlagAutoReranker`.

**RAC-22:**
- [ ] The candidate list is reranked and cut to a top-k set.
- [ ] Rerank improves precision@k over the raw fused list on the golden set.
- [ ] Registered under FR-C.4.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_reranking.py`

**Dependencies:** T21. **Scope:** M.
**Files:** `src/rag_wright/capabilities/reranking.py`, `tests/capabilities/test_reranking.py`

### GATE-2: Recall-bar, ArcadeDB hybrid vs LanceDB fallback (branch point)

**Not a task.** Validate ArcadeDB hybrid retrieval against the archetype recall bar (T9 + T10),
each leg measured separately (§12, plan §2).
- **Meets the bar →** continue with ArcadeDB.
- **Underperforms →** substitute LanceDB for the retrieval leg behind the query-skill seam
  (FR-S.5) — the one eval-gated fallback, not a default. Raise with the human; the seam (T13)
  already makes the swap local to the store implementation.

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
- [ ] Contract extraction, the spaCy NER/dependency path, and the LLM escalation each produce
  ontology-conforming facts carrying `chunk_id` + confidence.
- [ ] The LLM escalation uses the model-profile seam and the A-T2-recorded DeepSeek V4 Pro profile
  (no hardcoded flag).
- [ ] **FR-I.6 decoupling property:** extraction is built to be called concurrently and
  non-blocking, its model/GPU calls go through the poolable inference boundary, and it applies
  backpressure, so bulk mode can saturate the GPU. Verified by a concurrency test.
- [ ] Registered under FR-C.6.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_graph_extraction.py`

**Dependencies:** T5, T8, T16. **Scope:** L.
**Files:** `src/rag_wright/capabilities/graph_extraction.py`,
`tests/capabilities/test_graph_extraction.py`
**Note:** spaCy ships first; OpenIE slots behind the same extractor seam later (ADR-0001).

### Task T24: Entity resolution (closed-world to EDGAR CIK)

**Description:** Resolve extracted mentions to the registry's canonical `entity_id` (EDGAR CIK),
closed-world against the known set, so the graph does not fragment across surface-form variants
(FR-C.7). Decide and record the matching strategy (exact / fuzzy / embedding / LLM-assisted, §16.3)
at this task.

**RAC-24:**
- [ ] A known mention resolves to the correct EDGAR CIK `entity_id`; an unknown mention is handled
  per the closed-world policy (not silently fabricated).
- [ ] Fragmentation rate is measured on the golden set.
- [ ] **Post-resolution self-loop check (moved here from the T4 contract):** after resolving a
  `RelationshipFact`'s `source_ref`/`target_ref` to canonical `entity_id`s, a relationship whose two
  refs resolve to the *same* `entity_id` is collapsed/dropped as a self-loop. This belongs here, not
  in the T4 fact contract, because two distinct mentions can legitimately resolve to one entity.
- [ ] **Resolve both mention channels as one stream (from T5):** the standalone
  `EntityMention`s and the `RelationshipFact` `source_ref`/`target_ref` refs are the same
  surface-form notion (two channels for the same entities). Resolution must dedupe *across* both, so
  an entity appearing as both a standalone mention and a relationship endpoint resolves to a single
  node. Resolving the channels independently is where the duplicate-node fragmentation would occur —
  build the dedup across channels here rather than discover the double node in the graph eval.
- [ ] The chosen matching strategy is recorded (ADR).
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_entity_resolution.py`

**Dependencies:** T8, T23. **Scope:** M.
**Files:** `src/rag_wright/capabilities/entity_resolution.py`,
`tests/capabilities/test_entity_resolution.py`, `docs/adr/000X-entity-resolution.md`
**Note:** Resolves §16.3 matching strategy. Fragmentation is risk 5.

### Task T25: Graph storage (nodes/edges carry `chunk_id`, content-hash gated)

**Description:** Write nodes and edges into the same ArcadeDB store, carrying the originating
`chunk_id`, conforming to one ontology, gated by content hash (FR-I.4, FR-I.5). A chunk and its
extracted entities connect in one transaction (FR-S.1). The graph is the relationship layer only;
heavy structured data does not go in it (SPEC §8).

**RAC-25:**
- [ ] Nodes/edges are written carrying `chunk_id` and confidence; chunk and its entities connect
  in one transaction.
- [ ] Re-running an unchanged document does no graph work (content-hash gate).
- [ ] No heavy structured data is placed in the graph (relationship layer only).
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_graph_storage.py -m store`

**Dependencies:** T13, T23, T24. **Scope:** M.
**Files:** `src/rag_wright/capabilities/graph_storage.py`,
`tests/capabilities/test_graph_storage.py`

### Task T26: Graph query (cited `chunk_id`s, `entity_id`s, confidence)

**Description:** Answer relational and multi-hop questions via graph query, returning an answer
with cited `chunk_id`s, `entity_id`s, and confidence tags, from the same store as the retrieval
index; the answer is treated as evidence, not truth (FR-C.5, FR-Q.3, SPEC §8/§14).

**RAC-26:**
- [ ] A multi-hop question over the EDGAR-derived golden set (T10) returns an answer with cited
  `chunk_id`s, `entity_id`s, and confidence tags.
- [ ] The result is shaped as evidence for fusion (T27), not a final ranked list.
- [ ] Registered under FR-C.5.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_graph_query.py -m store`

**Dependencies:** T25. **Scope:** M.
**Files:** `src/rag_wright/capabilities/graph_query.py`, `tests/capabilities/test_graph_query.py`

### Task T27: Fusion (union/dedup on `chunk_id`, capped)

**Description:** Union and deduplicate on `chunk_id` between the reranked top set (T22) and the
graph-cited chunks (T26), capped; not a score fusion, since the graph returns an answer, not a
comparable ranked list (FR-Q.4).

**RAC-27:**
- [ ] The reranked top set and graph-cited chunks are unioned and deduplicated on `chunk_id`,
  capped at the union cap.
- [ ] It is deterministic and is not a score fusion.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_fusion.py`

**Dependencies:** T22, T26. **Scope:** S.
**Files:** `src/rag_wright/capabilities/fusion.py`, `tests/capabilities/test_fusion.py`

---

## Phase 4 — Build, RLM synthesis tier (spec Phase 3)

### Task T28: RLM synthesis (interpreter load, slice in code, recursive sub-calls)

**Description:** Using the RLM skill (T15), load the candidate chunks (T27) into an interpreter as
data, slice and filter in code, and recursively call sub-models on the small focused portions, so
it never attends over the full chunk volume (FR-Q.5). The recursive sub-calls are
structured-under-reasoning work, so they resolve to the DeepSeek V4 Pro default in the seam.

**RAC-28:**
- [ ] Candidate chunks load into the interpreter as data; slicing/filtering happens in code.
- [ ] Sub-model calls run on focused portions; the capability never attends over the full volume.
- [ ] Registered under the RLM synthesis capability.
- [ ] ARD-registered: the ARD manifest (skeleton from T6, representative queries authored) loads
  under `RegistryStore(root)` with no `RegistryLoadError` and is returned by `discover` for each
  representative query.

**Verification:** `uv run pytest tests/capabilities/test_rlm_synthesis.py`

**Dependencies:** T15, T27. **Scope:** L.
**Files:** `src/rag_wright/capabilities/rlm_synthesis.py`,
`tests/capabilities/test_rlm_synthesis.py`

### Task T29: Answer generator (grounded, cited, abstains) + vision-to-text

**Description:** Produce grounded, cited answers (no citation, no claim), confidence-aware, that
abstain when the retrieved context does not support an answer (FR-Q.6); plus the image-and-scan-
to-text step at ingestion (FR-C.9), on a Gemma 4 class model through the model-profile seam.

**RAC-29:**
- [ ] An answer carries a citation for every claim; an unsupported question yields an abstention,
  not a fabrication.
- [ ] The answer is confidence-aware (surfaces graph-fact confidence tags).
- [ ] Vision-to-text converts a scanned filing's images to text at ingestion.
- [ ] Registered under FR-C.9.
- [ ] ARD-registered: both the answer generator and vision-to-text manifests (skeletons from T6,
  representative queries authored) load under `RegistryStore(root)` with no `RegistryLoadError` and
  are returned by `discover` for their representative queries.

**Verification:** `uv run pytest tests/capabilities/test_answer_generator.py`

**Dependencies:** T11, T27. **Scope:** L.
**Files:** `src/rag_wright/capabilities/answer_generator.py`,
`src/rag_wright/capabilities/vision_to_text.py`,
`tests/capabilities/test_answer_generator.py`
**Note:** Enforces "no claim without a citation" and abstention (risk 9). Vision-to-text exercises
the scanned-filing subset (ADR-0002).

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
- [ ] All FR-C / FR-I / FR-Q capabilities built, tested, internally registered, and ARD-registered
  (every capability's ARD manifest loads and validates under `RegistryStore`). Both gates resolved
  and recorded. End-to-end bar met. Ready for the compiler to discover and bind the graphs from the
  Orchestration Spec.

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
| FR-C.7 entity resolution | T8, T24 |
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
grounding every call (T13, T21, T25, T26). Risk 4 (chunking determinism) is tested in T17. Risk 5
(entity-resolution fragmentation) is measured in T24. Risk 7 (identifier schemes) fixed at T1.
Risk 8 (summary-miss) is designed into T19 and tested there and at T32. Risk 9
(citation/abstention) is enforced at T29. Risk 10 (OpenIE undecided) is deferred behind the T5
extractor seam.
