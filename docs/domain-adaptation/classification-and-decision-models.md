# Classification & decision models

Much of a pipeline's per-unit work is not LLM-shaped. A "do everything" LLM call over every unit of a long document
is a bundle of separable decisions — and most of the bundle is cheaper, faster, and more robust as a deterministic
rule, a trained classifier, or a typed decision model. This page is the decision framework; the deep playbooks are
the skills linked below.

## 1. Find the opportunities — the `classifier-opportunity-analysis` skill

Map each per-unit LLM call and name the **shape** of each decision it bundles. Pick the mechanism cheapest-and-most-
robust first:

| decision shape | mechanism |
|---|---|
| boundary / "is this a unit / extractable?" | **deterministic structure** first (numbering, headings, layout); a small binary classifier only for the ambiguous residue |
| closed-vocab value with an enumerable cue list | a **deterministic cue-rule** (no ML) |
| single-label routing (closed set, no clean cues) | a **classifier** |
| multi-label soft-tagging (guidance, not a gate) | a **soft-tag classifier** (top-k) |
| verbatim text vs a paraphrase | extract the **verbatim span** (deterministic) |
| open / numeric / free-text / synthesis | keep the **LLM**, reduced to one residual call per unit |
| a verdict over a closed label set (compliant/violation, relevant/not) | a **classifier / decision model** (often query-time) |

Ingestion (per-unit over long documents) is the biggest win; query-time is low-volume and often fine to leave on
the LLM. Decide the **role** first: a *soft tag* that only augments is safe at modest accuracy; a *hard gate* needs
high accuracy + a graceful-degrade fallback.

## 2. Pick the builder — trained classifier vs System-1 decision model

For a closed-set decision, three realistic builders (A/B them on the same eval — see step 3):

- **Trained classifier (SetFit)** — a Sentence-Transformer body + a small head; CPU-cheap per unit, runs in the
  ingestion hot path, needs labeled data. The **`setfit`** skill: symmetric leakage-safe eval, per-class floor,
  soft-tag/top-k, rare-class curation, calibration, checkpointing, serve-behind-a-seam.
- **System-1 decision model (managed): Jev** — the OpenRouter Decisions API returns a calibrated typed answer
  (`noul`/`choice`/`score`) with **no training**, strong zero/few-shot (ADR-0119). Reach for it first when data is
  scarce or labels are ambiguous, or when you need calibrated uncertainty to route/gate. It's an I/O call, so it's
  cost-bounded to where you actually invoke it (a residue, a query step) — not every ingestion span.
- **System-1 decision model (open): Laya** — the open-weight sibling; near-random zero-shot, must be fine-tuned.
  Use when a managed API is unacceptable (on-prem/data-residency). The **`laya`** skill.

> Directional, from the reference project (re-measure on YOUR data): on one closed-set gate, zero-shot Jev reached
> the LLM's accuracy where a trained SetFit plateaued below it (ADR-0119). Numbers never transfer — the eval decides.

## 3. Decide with the eval — the `creating-evals` skill (first)

Write the capability's eval **before** you build it (TDD): a reproducible gold set + the metric + a gate from the
acceptance criterion. It is the neutral judge you **A/B** the options on — deterministic rule vs trained classifier
vs decision model vs LLM — selecting on the gate + diagnostics + cost + calibration. The training data you build IS
the eval. This is the engine's "identify → **eval-first** → build" chain.

## 4. Wire it as a capability (behind the seam)

- A trained classifier or a decision model is a **`model`-kind capability** with an `impl_ref`
  (`def <slug>(resources, inputs) -> result`), invoked by name through the engine
  ([authoring capabilities](authoring-capabilities.md)) — never hand-constructed around the invoker.
- A **decision model rides a `DecisionModelProfile`** (`rag_wright.models.profiles`, ADR-0119): the model id,
  served id, endpoint, `api_key_env`, timeout, and thresholds live in config (selected by `RAG_DECISION_MODEL`),
  so swapping Jev ↔ Laya ↔ a future open equivalent is configuration, not code. The `jev_decision` capability is
  the reference model-capability.
- The decision **questions/criteria live in the `.ttl`** (ADR-0066), not in the capability code — the ontology is
  the knowledge, the capability is the mechanism.
- Keep a **graceful-degrade path** (an abstention/low-confidence routes to the residual LLM or an explicit
  "ambiguous"), never a silent wrong answer.

## Skills

`classifier-opportunity-analysis` (identify) → `creating-evals` (eval-first) → `setfit` / `laya` (build the
decision) → `authoring-a-capability` (register). Bulk teacher-labeling for training data runs on the flat-GPU
substrate (`qwen-vllm-modal`), not ad-hoc paid calls.
