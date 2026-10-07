---
name: classifier-opportunity-analysis
description: >-
  Structured guide for ANALYZING a domain's ingestion + retrieval pipeline to find where an LLM call can be
  replaced by a deterministic rule, a trained classifier, or a routing decision. Use it BEFORE building or
  refactoring a domain pack, when an LLM is doing per-unit work that multiplies over a document, or when
  onboarding a new domain — it is the identification/decision step upstream of `setfit` (which BUILDS the
  classifier) and `authoring-a-capability` (which REGISTERS it as a capability). It captures the recipe applied
  twice (contracts, then compliance) so the next domain is mapped the same way instead of re-derived.
---

# Finding classifier / routing opportunities in a pipeline

This is an **analysis** skill: its output is a decision — an *opportunity list / decomposition plan*, not code and
not a trained model. It answers "where in this domain's ingestion and retrieval does a classifier, a routing
decision, or a deterministic rule belong, and where must the LLM stay?" Build what it identifies with the `setfit`
skill, serve the teacher with `qwen-vllm-modal`, and register each result as a capability with
`authoring-a-capability`.

## The core pattern (why this works, and why we have done it twice)

A per-unit "do everything" LLM extraction is almost never one decision. It is a **bundle of separable decisions**
wearing one prompt. Decomposed, most of the bundle is not LLM-shaped work:

- **boundaries** (where does a unit start / is this span worth extracting) are usually structural → deterministic;
- **closed-vocab tags** (the type, the role, the dimension values) are classification → a classifier, or a
  deterministic cue-rule when the cues are enumerable;
- only the **genuinely open part** (numbers, free text, synthesis) needs an LLM, and then only **one residual call
  per unit**.

Worked precedent in this engine:

| | Contracts (decomposed) | Compliance (the CIC arc) |
|---|---|---|
| Unit boundaries | deterministic (section numbering / headings) + a decision-model residue decider (one batched `noul` call for the uncertain lines; ADR-0122) | deterministic operative-rule spans + one `jev_decision` call per span for the operative gate (the default `extraction_backend="jev"`) |
| Closed-vocab tags | the 21-dim / 29-dim classifier fleet | deontic cue-rule + actor / claim-types in that same per-span Jev call |
| Open / numeric field | deterministic candidate spans + one Jev `choice` call per provision with candidates (none without); the LLM only as fallback (`RAG_RESIDUAL_EXTRACTOR=llm`). Value recall / precision 0.83 / 0.88 vs the LLM's 0.60 / 0.79 | applicability + evidence standard: one gated residual LLM call, only for a rule with a conditional or evidence cue (most skip it); the `"docling"` fallback keeps one LLM extraction per section |
| Extraction judge | the Layer-3 judge on Jev: one batched call per provision, 95.3% vs the LLM judge's 90.1% (192 blind hand-labelled cases); `RAG_SEMANTIC_JUDGE=llm` opts out | (none) |
| Text of the record | verbatim span | verbatim span (was a paraphrase) |
| Structure / graph extraction | once per document (parties), keep the LLM | once per document, keep the LLM |

Cost shape (live, 131 provisions of one contract): 215 Jev calls, 0 LLM calls, $0.013, against 277 calls (276 of them
LLM) and $0.277 on the LLM path (ADR-0040, ING-9 / ING-9b addendum). One batched call per unit is the shape to aim for.

The pattern is domain-independent. What changes per domain is the vocabulary and the document structure — which is
exactly what the phases below make you look at.

## Phase A — Map the pipeline as per-unit decisions

Enumerate every LLM call and, for each, its **unit** and how the unit COUNT scales:

- per **document** (parse, a party/graph-structure pass) — count ≈ corpus size; cheap per doc.
- per **section / chunk / segment / span** — count scales with **document length**. A 100-page document is
  thousands of these. **This is where the cost lives and where decomposition pays.**
- per **query** / per **candidate** / per **(claim, requirement) pair** — query-time; count ≈ traffic, usually a
  few per request (see Phase D).

Two things to separate immediately:

1. **Structure / connection extraction** (entities, parties, graph edges — "who/what is in this document and how is
   it connected") is a **once-per-document** pass. It is NOT the per-unit cost; leave the LLM there. Do not mistake
   it for the thing to decompose. (In this engine that is the docling-graph pass; it runs once per contract and once
   per regulation.)
2. **Per-unit semantic tagging** (what IS this unit, what are its typed properties) is the multiplying cost. This is
   the target.

## Phase B — Classify each decision by its shape, then pick the mechanism

For every per-unit decision, name its shape. The shape dictates the mechanism, in this order of preference (cheapest
and most robust first):

1. **Boundary / segmentation** — "does a new unit start here?", "is this span operative / extractable?" →
   **deterministic structure first** (numbering, enumeration `(a)(b)`, headings, list markers). Add a small
   **binary classifier** only for the residue where structure is ambiguous (the analog of a `is_extractable_span`
   model). Rarely needs an LLM.
2. **Closed-vocab with an enumerable cue list** — a value the ontology can map from a fixed set of trigger phrases
   (e.g. a deontic type from "must / shall / may not") → a **deterministic cue-rule, NO ML**. Do this before
   training anything; it is free and exact.
3. **Single-label routing** — one of a closed set, no clean cue list → a **classifier**.
4. **Multi-label soft-tagging** — several of a closed set, used as *guidance* not a gate → a **soft-tag classifier**
   (top-k). The most forgiving shape; a modest-accuracy model is still useful because wrong extra tags are cheap.
5. **Verbatim vs generated text** — if the record just needs the unit's text, extract the **verbatim span**
   (deterministic) rather than a generated paraphrase. Drops a generative LLM step and is more faithful for
   citation. Keep a paraphrase only if a human-readable restatement is a real requirement.
6. **Open / numeric / free-text / synthesis**: no closed set. Often still not LLM work: **propose candidates
   deterministically** (the numbers, amounts, durations in the unit), then a **decision model labels each
   candidate's role** in one batched call per unit. Author the roles and their one-line criteria in the pack `.ttl`
   (the reference pack's `cbr:ResidualRole` + `cbr:decisionCriterion`). Keep the LLM, as **one residual call per
   unit**, only for values no candidate generator can propose.
7. **Pair / entailment / verdict**: rerank a candidate against a query, or a judge verdict over a (subject, rule)
   pair → a **decision model** is a strong candidate (a verdict over a closed label set IS a classification), then
   a **cross-encoder / NLI classifier**. The ingestion extraction judge moved to a decision model (ING-9). Often
   query-time; see Phase D.

## Phase C — What to look for in the documents themselves

Signals that make decomposition **feasible** (push toward rules + classifiers):

- explicit **numbering / enumeration / heading** structure → deterministic boundaries;
- a **closed, ontology-authored vocabulary** for the typed fields → classifiers + cue-rules;
- **repeated template structure** across documents → stable features;
- **enumerable linguistic cues** (deontic verbs, defined terms, standard phrasings) → cue-rules.

Signals that **resist** it (keep the LLM): genuinely open / unbounded values, cross-document or multi-hop
reasoning, long-prose synthesis, values that depend on interpretation rather than surface form.

## Phase D — Ingestion vs retrieval: where the win actually is

- **Ingestion** per-unit work on long documents multiplies into thousands of calls. This is the **biggest, do-first**
  opportunity. The whole Phase B decomposition applies.
- **Retrieval / query time** is typically a **few calls per request** (classify the query, extract its constraints,
  rerank, judge). These ARE classifier/routing shapes (closed-set query routing, a pair-classifier reranker or
  judge), but the volume is low and you often want the LLM's rationale or synthesis. It is legitimate to **live with
  the LLM at query time** (as this engine does for the contract and compliance judges) and still decompose
  ingestion fully. State this as a deliberate choice per decision; do not reflexively de-LLM query time.

## Phase E — Soft-tag vs hard-gate: decide the role before committing

The same classifier is safe or dangerous depending on how its output is used:

- a **soft tag** that only augments / hints → safe even at modest accuracy; wrong extra tags are cheap.
- a **hard gate** that drops, blocks, or routes irreversibly → needs high accuracy AND a safe fallback.

Decide the role first. Keep a **graceful-degrade path**: a classifier abstention or a persistent rule-miss should
fall back to the residual LLM (or to an explicit "ambiguous"), never to a silent wrong answer.

## Phase F — Make it ttl-driven, and know what transfers across domains

Author the closed vocabulary and the cue lists in the **ontology (`.ttl`), never in Python** (the engine's
knowledge-in-the-ontology rule). Then:

- the **deterministic mechanism** (structure split + cue-rule + verbatim extraction) is domain-generic and
  **transfers to any pack for free** — it reads whatever vocab/cues the pack authors;
- a **trained classifier is vocabulary-specific** — a new-vocabulary domain pack trains **its own**. "Reuse across
  products" therefore means the *same mechanism + per-pack models*, not one model everywhere.
- A pack that only re-routes an existing vocabulary at query time (an override overlay) is NOT a new-vocabulary pack
  and needs no new ingestion classifier.

## The output: the opportunity list

Produce a decomposition plan, not prose:

1. **Headline the single biggest per-unit LLM cost** (the multiplying ingestion pass).
2. For **each decision** give: its unit, its shape (Phase B), the chosen mechanism (deterministic / cue-rule /
   classifier / residual-LLM / verbatim), and its role (soft-tag vs gate).
3. Separate an **ingestion bucket** (do first) from a **query-time bucket** (decide case by case; often live with
   the LLM).
4. Note the **ttl + per-pack** generality (what transfers, what each pack re-trains).
5. **Map each decision onto an ingestion hook** (`build_ingestion`, ADR-0124): a boundary decision →
   `BoundaryDecider` / `UnitGrouper`; closed-vocab tags → `SpanTagger`; residual values and the judge → `Extractor`.
6. Exclude anything that is not actually a per-unit cost (once-per-document structure extraction) and anything
   already settled (a decision an existing cue-rule covers).

## Hand-off (what to do with the opportunities)

- **Deterministic rule / cue-rule / span-split** → plain code in an ingestion hook (`Segmenter`, `UnitGrouper`,
  `SpanTagger`, passed to `build_ingestion`). NOT a capability; it is mechanism, and the knowledge it reads lives in the `.ttl`.
- **System-1 decision model (NO training)** → for a closed-set decision (yes/no, choice, score), A/B a decision
  model — **Jev** (managed, OpenRouter Decisions API, zero/few-shot, calibrated) or **Laya** (open, fine-tuned) —
  BEFORE committing to a trained classifier. It often wins when data is scarce or label-ambiguous, or when you need
  calibrated uncertainty to route/gate (measured: RAG_Wright CIC-1c — Jev zero-shot 0.92 vs a trained SetFit 0.82;
  ADR-0119). See `setfit` Phase 0.5 (the decision-vs-train A/B) and the `laya` skill; wire it as a `jev_decision`-style
  model capability with a `DecisionModelProfile`. **Evaluate this first; it may remove the need to train at all.**
  The decision path runs only when `jev_decision` is registered (from `engine_capabilities()`) AND
  `OPENROUTER_API_KEY` is set; otherwise the reference pack's decision paths degrade silently (to the deterministic
  rule or the LLM), so check both before reading a number.
- **Trained classifier** → build it with the **`setfit`** skill (framing, symmetric leakage-safe eval, per-class
  floor, soft-tag/top-k, rare-class curation, checkpointing). Serve the teacher / bulk-labeler with **`qwen-vllm-modal`**.
  Then register it as a capability with **`authoring-a-capability`**: a `kind="model"` capability with an
  `impl_ref` factory `def <slug>(resources, inputs)` over a cached checkpoint, invoked by name through the engine
  API: from your ingestion hook, call `ainvoke_model(slug, inputs, resources=ws)`, **routed THROUGH the
  capability layer, never hand-constructed around it.** A model impl may be sync (a classifier / XGBoost — run
  off-loop by `ainvoke_model`) or async (an LLM-backed cap — awaited by `ainvoke_model`).

## Anti-patterns (from the real sessions — do not repeat)

- **Letting classifier training cost/time decide whether an opportunity exists.** Identify opportunities by the
  *pattern* (per-unit closed-set decision on a long document); training ROI is a separate, later question.
- **Mistaking once-per-document structure extraction for the per-unit cost.** It is cheap; leave the LLM.
- **Claiming a judge/verdict step "can't classify."** A verdict over a closed label set (compliant / violation /
  needs-review, relevant / not) IS a classification; it is a legitimate pair-classifier candidate (usually
  query-time).
- **Hardcoding the vocabulary or cues in Python.** They belong in the `.ttl`; the mechanism reads them.
- **Training a classifier where an enumerable cue-rule already settles the decision** (the deontic-type case).
- **Forcing a hard gate where a soft tag suffices** — it imposes an accuracy bar you did not need.
- **Reflexively de-LLM'ing query time** — low volume + wanted rationale often make the LLM the right call there.
- **A router/cascade of specialists** — it multiplies errors (router acc × specialist acc). A flat classifier +
  multi-tag usually beats it (see `setfit`).
