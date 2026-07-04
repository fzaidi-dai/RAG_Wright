# plan.md: RAG_Wright build plan

Phase 1 output. Read-only planning against `SPEC.md` v0.1. This plans the **capability half**
only: each item builds and registers an FR-C / FR-I / FR-Q capability as ordinary tested
software. The ingestion and query **graphs** are compiled separately from the Orchestration
Spec by the GraphWright compiler and are not built here.

> Terminology: to avoid a collision, "Build phase" = the playbook's phases (0 setup … 5
> integrate). "Spec phase" = SPEC.md section 13's phased build (0 foundation … 4 extras). The
> capability build below runs inside the playbook's **Build phase (Phase 4)**, ordered by the
> spec's phased build.

> Validation corpus (resolved, see ADR-0002): the Contract Understanding Atticus Dataset
> (CUAD) plus SEC EDGAR entity data. CUAD's expert clause annotations are ground truth for the
> exact/lexical, semantic, and clause-finding archetypes; the EDGAR party-and-entity graph is
> what makes the relational and multi-hop archetype measurable, which is the entire reason the
> ArcadeDB graph layer exists.

---

## 1. Major components

Grounded API names below are confirmed against the framework Graphify graph (Phase 0).

### A. Foundations (gate everything; built first, spec Phase 0)

| Component | Spec ref | Package | Grounded surface |
|---|---|---|---|
| Shared contracts: `chunk_id`, `entity_id`, provenance/confidence, ontology models, extraction contracts | FR-S.2/3/4, §11 | `contracts/` | Pydantic v2 |
| Model-profile seam (OpenRouter default / local; structured output keyed by model id) | tech stack, assumption 2 | `models/` | `langchain_openai.ChatOpenAI`, `.with_structured_output()` |
| Store seam behind the query-skill interface; ArcadeDB schema + dense `LSM_VECTOR` and sparse `LSM_SPARSE_VECTOR` indexes | FR-S.1/5, §16.4 | `store/` | `arcadedb_python` `SyncClient`, `DatabaseDao`, `.vector_search()` |
| Ontology and registry derivation | FR-C.8, assumption 3 | `ontology/` | Pydantic; CUAD clause categories + EDGAR CIK |
| RLM skill — the **general** divide-and-conquer method (see note below) | FR-C.10 | `skills/` | authored `SKILL.md` |
| Golden evaluation set + harness by archetype | §12, §13 P0 | `eval/` | pytest |

**Phase 0 corpus tasks (CUAD + EDGAR):**
1. Acquire and subset CUAD to ~100–150 contracts (~100MB), deliberately including some scanned
   filings so the Docling parse and vision-to-text path is exercised. **Confirm the Creative
   Commons Attribution 4.0 (CC BY 4.0) license at download.**
2. Derive the ontology from CUAD's 41 clause categories plus party and entity types (Pydantic
   models). (Resolves SPEC §16.2.)
3. Build the entity registry from EDGAR Central Index Key (CIK) data; CIKs are the canonical
   `entity_id`s. Entity resolution is closed-world against this registry. (Resolves §16.3.)
4. Construct the archetype-split golden eval set: CUAD expert annotations give ground truth for
   the exact/lexical and semantic archetypes and for clause-finding answer-and-citation
   questions; **multi-hop questions are constructed from the EDGAR party-and-entity graph** for
   the relational archetype (not optional — CUAD alone is single-document clause extraction and
   under-tests the archetype the graph exists for).

**Two foundation tests (build the seam, then prove it — do not trust it):**
- **A-T1, ArcadeDB hybrid store-seam test.** Write a few records and run a `vector.fuse` hybrid
  query end to end to confirm the sparse index and RRF fusion behave as the docs suggest.
  ArcadeDB's hybrid features are recent; this surfaces a surprise while it is cheap and tells us
  early whether we are heading for the LanceDB fallback, rather than discovering it at the
  retrieval gate.
- **A-T2, model-profile seam forced-structured-output test.** Build the seam's foundation test
  around an **actual forced-schema call on the open model to be used for extraction** (the same
  failure surface the engine hit at T5.1: forced structured output on an open model in thinking
  mode). FR-C.6 depends on Pydantic-contract extraction, so learn the model's profile (method,
  structured-only extra body) here, before the extraction capability depends on it.

**Note on the RLM skill (FR-C.10).** The foundation item authors the RLM skill as the general
divide-and-conquer *method* (load a working set into an interpreter, slice and dispatch in code,
synthesize). **It has no testable behavior on its own.** Two capabilities each *apply* the
method with their own contract and tests: the **RLM chunking** capability (FR-I.1) owns
determinism, boundary validation, and content-hash gating; the **RLM synthesis** capability
(FR-Q.5) owns the recursive sub-call behavior. "RLM skill in foundations" must not be read as
building RLM behavior before a capability uses it.

### B. Phase 1 baseline — write-side and read-side (spec Phase 1)

Spec Phase 1 builds both the write-side capabilities and the read-side capabilities, because you
must query to validate that ingestion produced good records. The FR grouping is kept honest:
hybrid search and reranking are **query-side (FR-Q)**, not ingestion.

**Write-side (FR-C / FR-I):**
| Capability | Spec ref | Grounded surface |
|---|---|---|
| Parsing (Docling) | FR-C.1 | `docling` `DocumentConverter`; `docling_core` `HybridChunker` |
| RLM chunking (deterministic, gated; small→large escalation) | FR-I.1, FR-I.2 | RLM skill + model seam |
| Embedding (BGE-M3: dense over summary, sparse over full text) | FR-C.2, FR-I.3 | `FlagEmbedding` `M3Embedder` |
| Chunk record write + incremental upsert (content-hash gated) | FR-I.3, FR-I.5 | store seam |

**Read-side (FR-Q):**
| Capability | Spec ref | Grounded surface |
|---|---|---|
| Hybrid search (server-side RRF fusion, metadata filters) | FR-C.3, **FR-Q.1** | ArcadeDB `vector.fuse` |
| Reranking (cross-encoder precision gate) | FR-C.4, **FR-Q.2** | `FlagEmbedding` `FlagAutoReranker` |

### C. Graph layer (spec Phase 2)
| Capability | Spec ref | Grounded surface |
|---|---|---|
| Graph extraction (hybrid: contract + NER/dependency/OpenIE + LLM escalation) | FR-C.6, FR-I.4 | `spacy`; OpenIE deferred behind an extension point (see note) |
| Entity resolution (closed-world to EDGAR CIK registry) | FR-C.7 | registry + matching strategy TBD (§16.3) |
| Graph storage (nodes/edges carry `chunk_id`; content-hash gated) | FR-I.4 | store seam |
| Graph query (cited `chunk_id`s, `entity_id`s, confidence) | FR-C.5, FR-Q.3 | ArcadeDB graph query |
| Fusion (union/dedup on `chunk_id`, capped; not score fusion) | FR-Q.4 | deterministic |

**Note (OpenIE extension point).** Design the graph-extraction capability's contract with an
explicit extension point so the deferred Open Information Extraction (OpenIE) path can be added
behind it later without reopening the capability. The lightweight NER-plus-dependency path
(spaCy) ships first; OpenIE slots in behind the same contract.

### D. RLM synthesis tier (spec Phase 3)
| Capability | Spec ref | Grounded surface |
|---|---|---|
| RLM synthesis (interpreter load, slice in code, recursive sub-calls) | FR-Q.5 | RLM skill + model seam |
| Answer generator (grounded, cited, confidence-aware, abstains) + vision-to-text at ingestion | FR-C.9, FR-Q.6 | model seam (Gemma 4 class) |
| Prefix + result caching | §13 P3, §16.7 | TBD |

### E. Interface & extras
| Component | Spec ref | Notes |
|---|---|---|
| MCP skill surface (governed skills over MCP) | FR-S.5 | official `mcp` SDK; grows as capabilities register |
| Case-by-case extras: multi-vector, typed-functional RLM, CLIP embedder, canonical skeleton, domain LoRA | §3.2, §13 P4 | **out of scope / ask-first** |

---

## 2. Build order, dependencies, and the chunker gate

```
Contracts (chunk_id, entity_id, provenance, ontology, extraction)   [gates ALL]
        |
        +-- Model-profile seam  (+ A-T2 forced-structured-output test) --+
        +-- Store seam          (+ A-T1 vector.fuse hybrid test) --------+---> foundations
        +-- Ontology/registry   (CUAD categories + EDGAR CIK) -----------+     complete
        +-- RLM skill (general method only) ----------------------------+     (parallel after
        +-- CUAD+EDGAR corpus + archetype golden eval set --------------+      contracts)
                    |
   Phase 1 write-side:  Parsing -> RLM chunking -> Embedding -> Chunk write/upsert
                                        |
                             ┌──────────┴───────────────────────────────┐
                             │  GO / NO-GO GATE: A/B the RLM chunker     │
                             │  against a simpler baseline on the eval   │
                             │  set (boundary quality + summary fidelity)│
                             └──────────┬───────────────────────────────┘
                     beats baseline?    │    does NOT beat baseline?
                             │          │              │
                             ▼          │              ▼
     keep RLM chunker; build small→large │   DROP the small→large escalation and the
     escalation (FR-I.2) + full apparatus│   elaborate chunking apparatus; fall back to
                             │          │   the simpler chunker. Redirect the plan.
                             └──────────┤
                                        ▼
   Phase 1 read-side (FR-Q):  Hybrid search -> Reranking      [validates the write-side records]
                                        |
                             recall-bar gate: ArcadeDB hybrid meets archetype recall?
                                   yes -> continue    no -> LanceDB fallback behind the seam (FR-S.5)
                    |
   Graph layer (spec P2):  Graph extraction -> Entity resolution -> Graph storage -> Graph query -> Fusion
                    |
   RLM synthesis tier (spec P3):  RLM synthesis -> Answer generator (+ caching)
                    |
   Integrate (Build phase 5):  MCP skill surface + end-to-end scenarios + per-source ablation
```

**The RLM-chunker A/B is a branch point, not just a checkpoint** (Change 3): the spec requires
the RLM chunker to beat a simpler baseline before it earns its cost. If it does not, the
small-to-large escalation and the elaborate chunking apparatus do not get built.

**Sequential (hard dependencies):** contracts → everything; parsing → chunking/embedding/
extraction; ontology → extraction/resolution; store → storage/retrieval; RLM skill → RLM
chunking and RLM synthesis; write-side records → read-side validation; retrieval+rerank+graph →
RLM synthesis.

**Parallelizable:** after contracts, the four foundation seams (model, store, ontology, RLM
skill) plus the corpus/eval build are independent. Within Phase 1, embedding and (later) graph
extraction both consume parsed documents.

---

## 3. Risks and mitigations

1. **ArcadeDB hybrid retrieval may miss the recall bar** → eval-gated LanceDB fallback for the
   retrieval leg behind the query-skill seam (FR-S.5). De-risked early by foundation test A-T1.
2. **`arcadedb-python` is v0.x** → maturity/API-drift risk. Ground every call against the
   framework graph (the driver is 129 nodes in the index); A-T1 exercises it end to end.
3. **Structured output on open models in thinking mode** (Qwen/DeepSeek reject forced schema)
   → model-profile seam (method + structured-only extra body keyed by model id), flags empirical
   and in a dated ADR, never hardcoded. De-risked early by foundation test A-T2.
4. **RLM chunking determinism** (FR-I.1) → temperature zero or structured output, boundary
   validation, content-hash gate; determinism must be testable.
5. **Entity-resolution fragmentation** (FR-C.7) → closed-world against the EDGAR CIK registry;
   measure fragmentation in eval.
6. **CUAD is single-domain legal** → the generic-RAG generality claim needs validation on a
   second domain later (ADR-0002). A property of the eval, not a blocker.
7. **Identifier schemes are load-bearing** (FR-S.2/3) → fixed in the contracts phase; changing
   them is ask-first.
8. **Summary-miss failure mode** (§12) → the dense-over-summary + sparse-over-full-text split is
   designed to prevent it; test it explicitly on the CUAD golden set.
9. **No claim without a citation / abstention** (FR-Q.6) → enforced in the answer generator and
   tested.
10. **OpenIE tool undecided** (FR-C.6) → deferred behind the graph-extraction contract's
    extension point; spaCy covers NER + dependency parsing until then (ask-first dependency).

---

## 4. Verification checkpoints (from SPEC.md §12, on the CUAD+EDGAR golden set)

- **Per capability:** each FR-C is a tested unit with its own contract, verified in isolation.
- **Foundation tests:** A-T1 (ArcadeDB `vector.fuse` hybrid end to end) and A-T2 (forced
  structured output on the open extraction model) run before the capabilities that depend on them.
- **Retrieval:** recall@k per archetype, each leg (text, graph) measured separately.
- **Summary-miss:** the specific dropped-detail failure mode.
- **Chunk quality:** boundary quality + summary fidelity; **A/B the RLM chunker vs a baseline as
  the go/no-go gate** (section 2), not merely a checkpoint.
- **Entity resolution:** correct EDGAR CIK `entity_id`, fragmentation rate.
- **End-to-end:** answer quality, faithfulness, citation correctness; abstention when unsupported.
- **Latency:** round-trip and tail (routing thresholds are a tuning input; routing is orchestration).
- **Per-source ablation:** turn off text retrieval, then graph, then RLM synthesis; measure the
  loss by archetype (the EDGAR-derived multi-hop questions are what make the graph leg measurable).

---

## 5. Open questions carried into Tasks/Contracts (SPEC.md §16)

Resolved by ADR-0002 / the CUAD+EDGAR choice: §16.2 (ontology = 41 CUAD clause categories +
party/entity types; registry = EDGAR CIK as canonical `entity_id`s) and §16.3 (resolution is
closed-world against the EDGAR CIK registry; matching strategy still open). Still open and
resolved at their tasks: chunking-skill internals (§16.1); ArcadeDB schema/index specifics
(§16.4); retrieval parameters (§16.5); routing thresholds (§16.6); caching design (§16.7);
ingestion pipeline build (§16.8); serving/infra and local-mode GPU (§16.9); domain LoRA (§16.10).

---

## 6. What is NOT built here

The ingestion graph and the query graph (compiled from the Orchestration Spec by the GraphWright
compiler); any node/edge/orchestration wiring; and the spec Phase 4 extras until a use case and
the eval set justify them (ask-first).
