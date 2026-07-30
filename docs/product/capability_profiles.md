# Capability profiles — kinds, contracts, and maturity

**Status:** design spec for review (2026-07-30). Drives the ARD registration + LangGraph hardening work.
Generic capability names (not "Leg-A/B/C" — those aren't generic). Uses **GraphWright's kind taxonomy only**:
`agent_skill`, `mcp_tool`, `function`, `model`, `subgraph`, `dagster_asset` (the last unused — we handle
document corpora, not structured-data assets).

## Kind-decision rule (settled)

| Kind | Use when |
|---|---|
| `function` | pure deterministic compute, no model weights, no LLM |
| `model` | trained ML inference (classifier / embedder / reranker); deterministic output, needs weights (CPU/GPU auto-detected) |
| `agent_skill` | a **single LLM act**, dynamically loadable (context economy inside a parent graph) |
| `subgraph` | a **multi-step deterministic LangGraph workflow** (even "1 LLM call + 1 function call"); permanent system prompts; invoked as a subagent under a contract |
| `mcp_tool` | *delivery* surface for an external orchestrator — a product-layer wrapper over a capability (not registered here) |

Resolved decisions: (1) `model` is a real kind — use it. (2) Any multi-step sequence is a `subgraph`; the
"atomic vs composite" framing is dropped. (3) Robustness (escalation, reground, retry, exception, HITL) is
**internal LangGraph hardening**, not separate capabilities — from the registry's view these are just
`subgraph`s. (4) Anything with an LLM call is `agent_skill` (1 call) or `subgraph` (multi-step); the old
`function` label on LLM-backed capabilities is corrected.

Everything ultimately runs, in GraphWright, either as a DeepAgent flow (dynamic) or a deterministic flow
(skills + tools + subgraphs wired by LangGraph, no DeepAgent decisions). Here we build the reusable capabilities
— deterministic workflows (`subgraph`), `agent_skill`s, `function`s, `model`s. A `subgraph` may later wrap a
DeepAgent workflow and still register as `subgraph`, decided case by case.

---

## Profiles by kind

### `model` — trained ML inference
| Capability | Contract (in → out) | Backing |
|---|---|---|
| `clause_function_classification` | span text → function label(s) | fine-tuned LegalBERT |
| `text_embedding` | text → dense + sparse vectors | BGE-M3 |
| `cross_encoder_rerank` | query + candidates → reranked | BGE-reranker |

### `function` — pure deterministic
| Capability | Side | Contract (in → out) |
|---|---|---|
| `document_parsing` | ingest | file → structured doc |
| `operative_span_segmentation` | ingest | chunk → operative spans |
| `extraction_grounding_judge` | ingest | typed record + text → confidence-tagged record (ADR-0028; also the permanent final-graph gate) |
| `typed_value_normalization` | ingest | (dim,val) → canonical value (gazetteer + subsumption) |
| `entity_resolution` | ingest | mention cluster → canonical id |
| `typed_kg_write` | ingest | records → ArcadeDB (content-hash gated) |
| `candidate_routing` | query | constraints + function preds → candidate pool (the union combiner) |
| `typed_constraint_match_rank` | query | constraints + pool → graded order (count-match + subsumption) |
| `dense_rank_tiebreak` | query | query + candidates → cosine order |
| `hybrid_search` | query | query → RRF candidates |
| `intra_document_scoped_query` | query | contract_id + filter → clauses |
| `clause_disambiguation` | query | contract_id + condition → the clause |
| `relational_graph_query` | query | entity + relation → paths |
| `requirement_applicability_retrieval` | compliance | claim + requirement KG → applicable reqs |
| `compliance_gap_report` | compliance | findings → cited gap matrix |

### `agent_skill` — single LLM act
| Capability | Side | Contract (in → out) |
|---|---|---|
| `query_function_classification` | query | query → function label(s) (taxonomy-constrained) |
| `grounded_answer_generation` | query | question + cited context → cited answer (abstaining) |
| `listwise_llm_rerank` *(optional)* | query | query + top-K → reorder |

`semantic_chunking` was previously listed here; it is now a **`subgraph`** (LLM propose boundaries →
boundary-validation → content-hash gate is a multi-step deterministic workflow).

### `subgraph` — multi-step deterministic LangGraph workflow

Component subgraphs:
| Capability | Side | Contract (in → out) | Internal shape (LangGraph nodes) |
|---|---|---|---|
| `semantic_chunking` | ingest | doc → chunk boundaries | LLM propose boundaries → validate → content-hash gate (single-call or RLM discoverer) |
| `typed_clause_extraction` | ingest | clause text + schema → confidence-tagged typed record \| dead_letter | extract(LLM) → adapt → reground → [escalate loop] → [HITL gate] |
| `graph_extraction` | ingest | chunk → entities/relations | NER + OpenIE + LLM stack |
| `query_constraint_extraction` | query | query → typed constraints | extract(LLM) → adapt → reground → [retry] |
| `claim_extraction` | compliance | subject doc → typed claims | extract(LLM) → adapt → reground |
| `regulatory_requirement_extraction` | compliance | reg text + schema → requirement KG | multi-pass scope / condition / deontic |
| `compliance_judgment` | compliance | claim + requirement → verdict + rationale + both-side cites | entailment(LLM) → [Flash→Pro escalate] → conservative-default → [HITL gate] |

Composite pipeline subgraphs (each wrapped as one LangGraph workflow):
| Capability | Contract (in → out) |
|---|---|
| `contract_ingestion_pipeline` | source docs → populated typed KG |
| `intra_document_qa` | contract + question → cited answer |
| `cross_corpus_retrieval` | corpus + query → ranked cited clauses |
| `relational_qa` | entity question → cited answer |
| `document_compliance_review` | subject doc + standard → cited gap report |

### `typed_clause_extraction` — the hardened subgraph shape (reference)
- **`extract`** (LLM) — granite + clause_template structured call; **RetryPolicy** on the real failures (empty
  result under wrong structured method, "no models", truncated JSON).
- **`adapt`** (function) — `Clause` → `ClausePropertyRecord`.
- **`reground`** (function) — grounding judge; ungrounded EXTRACTED → AMBIGUOUS.
- **escalate** (conditional edge) — low grounded-yield / low confidence → Flash→Pro re-extract (bounded loop).
- **`human_gate`** (`interrupt()`) — optional, low-confidence / high-stakes clauses.
- **exception / dead-letter** — per-node try/except → dead-letter on persistent failure (one bad clause never
  kills the batch). Contract: `{clause_text, function_hint, chunk_id, span_id} → {ClausePropertyRecord | dead_letter(reason)}`.

---

## Maturity table (what's done / needs update / roadmap)

**Honest baseline: LangGraph is not a dependency and no capability is implemented as a LangGraph today.** The
logic exists as procedural Python (classes / eval pipelines) and is tested; the Flash→Pro escalation is
procedural (`property_extractor.py`); checkpoints/dead-letter are bespoke files (`chunk_write`, `graph_storage`).
So every `subgraph` needs a LangGraph wrap + hardening.

Legend — Impl+Tested: ✅ yes / 🟡 prototype / ✗ no. Registered: ✅ / ✗ / ⚠︎ wrong-kind.

| Capability | Target kind | Impl+Tested | LangGraph | Registered | Status → Action |
|---|---|---|---|---|---|
| `document_parsing` | function | ✅ | n/a | ✅ | **Available** |
| `operative_span_segmentation` | function | ✅ | n/a | ✗ | register |
| `extraction_grounding_judge` | function | ✅ | n/a | ✗ | register (reused as final-graph gate) |
| `typed_value_normalization` | function | ✅ | n/a | ✗ | register |
| `entity_resolution` | function | ✅ | n/a | ✅ | **Available** |
| `typed_kg_write` | function | ✅ | n/a | ✗ | register |
| `candidate_routing` | function | ✅ (in eval) | n/a | ✗ | **package** out of eval + register |
| `typed_constraint_match_rank` | function | ✅ (in eval) | n/a | ✗ | package + register |
| `dense_rank_tiebreak` | function | ✅ (in eval) | n/a | ✗ | package + register |
| `hybrid_search` | function | ✅ | n/a | ✅ | **Available** |
| `intra_document_scoped_query` | function | ✅ | n/a | ✗ | register |
| `clause_disambiguation` | function | ✅ | n/a | ✗ | register |
| `relational_graph_query` | function | ✅ | n/a | ✅ | **Available** (as graph_query) |
| `requirement_applicability_retrieval` | function | ✗ | n/a | ✗ | **roadmap** (reuses match-rank) |
| `compliance_gap_report` | function | ✗ | n/a | ✗ | **roadmap** |
| `clause_function_classification` | model | ✅ | n/a | ✗ | register as `model` |
| `text_embedding` | model | ✅ | n/a | ⚠︎ function | **reclassify** → model |
| `cross_encoder_rerank` | model | ✅ | n/a | ⚠︎ function | **reclassify** → model |
| `query_function_classification` | agent_skill | ✅ | n/a | ✗ | register as `agent_skill` |
| `grounded_answer_generation` | agent_skill | ✅ | n/a | ⚠︎ function | **reclassify** → agent_skill |
| `listwise_llm_rerank` | agent_skill | ✅ (in eval) | n/a | ✗ | keep optional; package + register if used |
| `semantic_chunking` | subgraph | ✅ | ✗ | ⚠︎ agent_skill | **reclassify** → subgraph + wrap (currently rlm_chunking) |
| `typed_clause_extraction` | subgraph | ✅ (procedural) | ✗ | ✗ | **LangGraph wrap + harden** + register |
| `graph_extraction` | subgraph | ✅ | ✗ | ⚠︎ function | **reclassify** + wrap + harden |
| `query_constraint_extraction` | subgraph | ✅ (in eval) | ✗ | ✗ | package + wrap + harden + register |
| `claim_extraction` | subgraph | ✗ | ✗ | ✗ | **roadmap** |
| `regulatory_requirement_extraction` | subgraph | ✗ | ✗ | ✗ | **roadmap** |
| `compliance_judgment` | subgraph | 🟡 (C-6 core only) | ✗ | ✗ | **roadmap** (extend prototype → escalate+HITL) |
| `contract_ingestion_pipeline` | subgraph | ✅ (scripts) | ✗ | ✗ | wrap as LangGraph + register |
| `intra_document_qa` | subgraph | ✅ (serve) | ✗ | ✗ | wrap + register |
| `cross_corpus_retrieval` | subgraph | ✅ (eval kg_primary) | ✗ | ✗ | wrap + register |
| `relational_qa` | subgraph | ✅ | ✗ | ✗ | wrap + register |
| `document_compliance_review` | subgraph | ✗ | ✗ | ✗ | **roadmap** |

### Summary of the work implied
- **Available now (correctly registered):** `document_parsing`, `entity_resolution`, `hybrid_search`,
  `relational_graph_query` (+ existing `fusion`, `chunk_read`, `vision_to_text`, `rlm_*`, `okf_*` outside this
  contract set).
- **Reclassify (5 existing manifests):** `text_embedding`, `cross_encoder_rerank` → `model`;
  `grounded_answer_generation` → `agent_skill`; `graph_extraction` → `subgraph`; `semantic_chunking`
  (rlm_chunking) → `subgraph`.
- **Package out of `eval/` + register:** the retrieval core — `candidate_routing`,
  `typed_constraint_match_rank`, `dense_rank_tiebreak`, `query_constraint_extraction`, `cross_corpus_retrieval`.
- **Register the built contract-KG functions/skills:** value normalization, scoped queries, disambiguation,
  grounding judge, span segmentation, KG write, `query_function_classification`,
  `clause_function_classification`.
- **LangGraph wrap + harden (all subgraphs):** add `langgraph` as a dependency (**ask-first**); build
  `typed_clause_extraction` first (the reference), then the other component + composite subgraphs.
- **Roadmap (compliance, unbuilt):** `claim_extraction`, `regulatory_requirement_extraction`,
  `compliance_judgment` (extend the C-6 prototype), `requirement_applicability_retrieval`,
  `compliance_gap_report`, `document_compliance_review`.

> Note: adding `langgraph` is a new dependency — ask-first per CLAUDE.md before `uv add langgraph`.
