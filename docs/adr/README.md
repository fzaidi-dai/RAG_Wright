# Architecture Decision Records — index

127 ADR files (0001–0119, plus 0120 — the former duplicate `0015`, renumbered 2026-10-05 to repair the collision —
and 0121–0127). ADRs are an
immutable, cross-referenced decision log: nothing here is moved or renumbered — legacy records carry a `Status:`
banner pointing to what replaced them. This index groups them so a newcomer isn't drowned.

**Read first (the pivot):** [ADR-0052](0052-engine-product-split-graphwright-parked.md) — the engine/product split (open-core);
GraphWright parked. It supersedes the earlier two-repo "capability half + Orchestration Spec + GraphWright compiler"
framing that still appears in older ADRs and the root `SPEC.md`/`plan.md`.

## Start here — the current architecture spine

| ADR | What it establishes |
|---|---|
| 0052 | Engine/Product split (open-core); GraphWright parked — **the pivot** |
| 0117 | The engine API layer + capability runtime (engine-as-platform boundary) |
| 0118 | Engine core API vs ARD; the adapter-free `impl_ref` ARD invoker |
| 0066 | The ontology `.ttl` is the single runtime source of truth for domain knowledge (DRAFT) |
| 0067 | Domain-pack retargeting — decouple KG schema + ER from contracts/CUAD/EDGAR (DRAFT) |
| 0007 | The ArcadeDB store seam and hybrid schema (one store) |
| 0033 | One unified contract KG; the three legs are scoped queries over it |
| 0057 | Async engine architecture (true wall-clock cancellation) |
| 0045 | Structured output via client-side XML-tag parsing, app-wide and LLM-agnostic |
| 0103 | A Clause is a PROVISION (numbered section), not a span |
| 0114–0116, 0119 | Classifier-first extraction (SetFit ensemble) + Jev typed-decision model |
| 0030, 0039, 0110 | Model-training standard; self-hosted Modal/A100 substrate; the Qwen serving config |

## Park / supersession map

- GraphWright compiler + the RLM-as-interpreter / dynamic-sub-agent runtime + the OKF experiment → **parked by
  ADR-0052**; ARD registration reframed by **ADR-0117 / ADR-0118**.
- ADR-0006 → superseded by **ADR-0045** · ADR-0032 → superseded by **ADR-0045**
- ADR-0036 → retired by **ADR-0091** (lookup restored by ADR-0093)
- ADR-0040 → revised by **ADR-0082** (symbolic gate made function-independent)
- ADR-0048 → shelved behind a flag by **ADR-0114**
- ADR-0056 → superseded by **ADR-0057** · ADR-0087 → superseded by **ADR-0088** · ADR-0108 → superseded by **ADR-0110**
- ADR-0012 point 4 (the `en_core_web_sm` wheel-URL pin) → superseded by **ADR-0121** (spaCy is an optional extra;
  the model is a runtime download). The rest of ADR-0012 stands.
- Retirements that are themselves *current* decisions: 0043 retires `cross_corpus_retrieval`; 0047 retires the
  precomputed function gate; 0091 retires the `PartyTo` edge.

## Parked — GraphWright / RLM-interpreter / OKF era (superseded framing; kept for history)

| ADR | Title | Note |
|---|---|---|
| 0003 | ARD registration — schema mirroring and capability kinds | ARD retained; framing reframed by 0117/0118 |
| 0015 | RLM is interpreter + dynamic sub-agents; `grantedSubagents` populated | interpreter runtime parked |
| 0120 | RLM sub-agent identity + dynamic-dispatch trigger | formerly a duplicate 0015; renumbered |
| 0017 | Dynamic-dispatch trigger as a typed `skill_runtime` flag | parked |
| 0018 | The RLM method is the orchestrator's system prompt | parked |
| 0020 | Serialize interpreter sessions per process (KI-1) | no interpreter runtime now |
| 0021 | `capabilityInterface` governed typed I/O on ARD manifests | GraphWright vendor ext → 0117/0118 |
| 0022 | FR-K embedding-free OKF navigation (experimental) | shelved by 0025, retired by 0046 |
| 0023 | Cheap model role for OKF signpost enrichment | OKF retired (0046) |
| 0024 | OKF reader parallelism in a Python PTC tool | OKF retired (0046) |
| 0009 | RLM semantic chunking is a retained, configurable capability | RLM decision, parked for now |
| 0014 | Split FR-C.9 into `generation` and `vision_to_text` | GraphWright discovery-era, parked for now |
| 0016 | RLM is defined by required capabilities, enforced as tests | RLM decision, parked for now |
| 0019 | Recursion in chunking / synthesis | RLM decision, parked for now |

> **Parked, not deleted:** the RLM/generation/vision capability code (authored skill content +
> `rlm_chunking` / `rlm_synthesis` / `generation` / `vision_to_text`) may still exist in the tree, but these RLM/
> GraphWright-era decisions (0009/0014/0016/0019 and the interpreter-runtime + OKF set above) are **parked for
> now** and are not treated as part of the current supported surface. Revive or update them if a future need
> arises.

## Superseded / revised / shelved by a later ADR (kept for history)

| ADR | Title | Replaced by |
|---|---|---|
| 0006 | Empirical model profiles for structured output under reasoning | 0045 |
| 0032 | NL→type via two-step reason→emit on Gemma | 0045 |
| 0036 | The `PARTY_TO` edge | 0091 (lookup → 0093) |
| 0040 | Neuro-symbolic SHACL validation + narrowed semantic judge | revised by 0082 |
| 0048 | INGEST-LLM-CLASSIFIER (LLM clause classification pass) | shelved by 0114 |
| 0056 | Bound the structured-call retry to one layer | 0057 |
| 0087 | Carry the fused retrieval score on RankedSpan | 0088 |
| 0108 | Qwen3.8-27B single-A100 serving profile (interim) | 0110 |

## Current — everything else (by number)

0001 Stack and core library choices ·
0002 Validation corpus — CUAD + SEC EDGAR ·
0004 Entity disambiguation and canonicalization ·
0005 The relational + multi-hop golden set ·
0008 Unified framework index — LLM-extracted docs alongside Python AST ·
0010 transformers pinned below 5 for the FlagEmbedding reranker ·
0011 CUAD is an extraction benchmark, not a retrieval one — ACORD supplies queries ·
0012 EntityMention carries confidence; no proximity edges ·
0013 Entity resolution matching strategy — exact normalized, closed-world ·
0025 Retrieval pivot — function-classify → property-graph → rerank ·
0026 Demand-driven property schema + extended function taxonomy ·
0027 Route DeepSeek V4 Pro by OpenRouter throughput ·
0028 Deterministic property-grounding judge + Flash→Pro cascade ·
0029 The (b) retrieval pipeline is domain-portable ·
0030 Model-training standard — Modal, checkpointed, best-model, versioned ·
0031 Single-call boundary discovery for structured-contract chunking ·
0033 One unified contract KG; three legs scoped over it ·
0034 Granite 4.1-8b uses `json_schema` structured output ·
0035 Re-back `graph_extraction` with the GP-1B docling-graph extractor ·
0037 The clause template is authoritative hand-maintained code ·
0038 The full 506-contract CUAD KG on a GCP GPU VM + GCS backup ·
0039 Self-hosted open-model stack on Modal/A100 is the product substrate ·
0041 Capability-kind rubric and agent-skill runtime tiers ·
0042 Persist the clause-level span_id; backfill by content-hash join ·
0043 Retire `cross_corpus_retrieval`; standardize on Leg B ·
0044 The IS_EXCEPTION_TO derived relationship (DRAFT) ·
0045 Structured output via client-side XML-tag parsing ·
0046 ACORD folded into the one production KG; OKF span retired ·
0047 Retire the precomputed clause-function pre-filter ·
0049 The generic-customer lens for ingestion/KG moves ·
0050 Async, LangGraph-based ingestion (submit + status) ·
0051 Schema bootstrap + feedback-driven ontology evolution (DRAFT) ·
0052 Engine / Product split (open-core); GraphWright parked ·
0053 Answer-generation output hygiene ·
0054 Remove the KG function label from generator evidence ·
0055 Confidence delivered out-of-band as a hedging directive ·
0057 Async engine architecture ·
0058 Structure-first chunking ·
0059 Retrieval recall decoupled from classification; no silent loss ·
0060 Scope a compliance check to named policy sources ·
0061 Check a subject DOCUMENT per section ·
0062 Tiered OCR — fast engine, scan gate, VLM escalation ·
0063 Per-sentence compliance subject facts ·
0064 Typed properties travel out-of-band on EvidenceItem ·
0065 Deontic type + actor as primary applicability gates ·
0066 The ontology `.ttl` is the single runtime source of truth (DRAFT) ·
0067 Domain-pack retargeting (DRAFT) ·
0068 The compliance actor gate is recall-first ·
0069 The chunker consumes the reading-order body (tables/figures retrievable) ·
0070 Text-layer-first parsing — born-digital is authoritative ·
0071 De-fragmentation — reconstruct paragraphs ·
0072 Clause-extraction guard — skip furniture ·
0073 Lower the born-digital text-layer threshold ·
0074 Retry a docling ExtractionFailed (transient) ·
0075 Per-page VLM escalation ·
0076 The shared BGE model is serialized across threads ·
0077 Function classification runs concurrently across chunks ·
0078 A dedicated executor for network-bound extraction ·
0079 Product default granite-4.1-8b → 4.2-8b + OpenRouter routing ·
0080 Nested-schema tag parsing + re-ask-then-omit degrade ·
0081 Function-independent thematic-group clause extraction ·
0082 The symbolic clause-validation gate becomes function-INDEPENDENT ·
0083 Extraction offload carries trace context across the executor hop ·
0084 Gleaning configurable; off on the query leg ·
0085 Query constraint extraction via client-side tag-parse ·
0086 Recover OpenRouter's real cost from the streaming path ·
0088 A per-span relevance VERDICT on the corpus retrieval path ·
0089 The forced-structured path emits a Langfuse generation ·
0090 Extract AFFILIATE_OF during ingestion ·
0091 Retire the `PartyTo` edge and `party_clause_linking` ·
0092 `write_graph` idempotent; endpoint filters use `outV()`/`inV()` ·
0093 `entities_by_name` — a name→entity lookup on the Store seam ·
0094 A workspace `documents` scope on retrieval and traversal ·
0095 Carry parse-time page provenance through to the span ·
0096 Keyword-fallback normalization for value-bearing list dimensions ·
0097 The ingest extraction models are caller-configurable ·
0098 The `documents` scope is per-invoke on typed-property retrieval ·
0099 An MCP tool never takes a model-supplied tenant ·
0100 A model string carries its own access — profile-based routing ·
0101 Untagged spans reach clause extraction; aspect gate removed ·
0102 Open descriptive list-dims retain verbatim ·
0103 A Clause is a PROVISION, not a span ·
0104 Dense floor-protection in Leg B ·
0105 In-band model usage accounting ·
0106 Judge-only document signals stay off the citation ·
0107 Page provenance on a Requirement ·
0109 Cutting Qwen3.8-27B cold start toward sub-60s ·
0110 FP8 KV cache — Qwen3.8-27B 16K at ≥20× concurrency on one A100 ·
0111 Pin Qwen3.8-27b to DeepInfra's bf16 endpoint (superseded by 0125) ·
0112 Relax the `deepagents` pin to a `>=0.7.15` floor ·
0113 Real span durations, TTFT, retrieval spans, provider generation ids ·
0114 Trained SetFit ensemble is the default clause-function classifier ·
0115 Classifier-only Step-3a property extraction ·
0116 Soft function-scoping for the classifier lane ·
0117 The engine API layer + capability runtime ·
0118 Engine core API vs ARD — adapter-free `impl_ref` client ·
0119 Jev typed-decision model for compliance closed-set decisions ·
0121 spaCy is an optional extra; its model is a runtime download (supersedes ADR-0012 point 4) ·
0122 Provision-boundary detection is deterministic-first with a decision-model (Jev) fallback for the residue ·
0123 Batched engine releases via release-please; products consume releases via Dependabot ·
0124 A generic ingestion builder with domain hooks; the legal pipeline becomes the reference instantiation ·
0125 Qwen3.8-27b on OpenRouter is unpinned (no provider routing; supersedes 0111) ·
0126 Which span represents a unit is a domain hook; the reference pack votes over operative spans ·
0127 Retire the OKF code
