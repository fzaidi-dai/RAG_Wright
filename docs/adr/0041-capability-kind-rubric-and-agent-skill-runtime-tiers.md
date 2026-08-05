# ADR-0041: Capability-kind rubric and agent-skill runtime tiers (SKILL-SPLIT)

Date: 2026-08-05
Status: Accepted

## Context

Registered capabilities carry a `kind` (`ard.py`: `function | model | agent_skill | subgraph | mcp_tool |
dagster_asset`). Over time several LLM-bearing capabilities had been registered as `function` — the judging
outlier `extraction_semantic_judge`, and the compliance additions `compliance_judgment`, `claim_extraction`,
`requirement_extraction`, plus the ingestion-side `vision_to_text`. That is a category error: a `function` is a
deterministic in-process node, and a node that constructs or receives a model and invokes it is not
deterministic. The codebase's own precedent already said so — `generation` is an `agent_skill` ("a single
grounded/cited LLM act", CAP-REG-1) — but the rule had never been written down, so the outliers accreted.

A second, subtler question surfaced once the kinds were corrected: **how is an `agent_skill`'s runtime actually
realized?** A Skill (agentskills.io / the standard Claude supports) is a *folder* — `SKILL.md` (frontmatter +
method) plus optional assets/scripts/templates — and can be loaded live by `SkillsMiddleware` with progressive
disclosure, or driven by a full Deep Agent. But most of our skills are single, self-contained LLM acts. We
needed a rule for *which* runtime a skill gets, so we neither under-build (a raw prompt string) nor over-build
(a Deep Agent for a one-shot structured call).

## Decision

### 1. The capability-kind rubric (what each kind means, enforced going forward)

- **`function`** — deterministic, in-process, **takes no model and constructs none**. Parsers, embedder/reranker
  glue, fusion, ranking, normalization, the deterministic gates (`extraction_grounding_judge`,
  `extraction_semantic_gate`, `compliance_finding_assembly`, `requirement_adaptation`, `claim_adaptation`). If it
  receives a model as an argument or builds one, it is **not** a function.
- **`model`** — a trained-model inference call (BGE-M3 `embedding`, BGE cross-encoder `reranking`, LegalBERT
  `clause_function_classification`).
- **`agent_skill`** — a **single grounded LLM act**, authored as a `skills/<name>/SKILL.md` folder and applied
  through the model-profile seam. Loaded knowledge, not a deterministic callable (no `response_bounds`).
- **`subgraph`** — a **multi-step workflow** (a compiled LangGraph). Used when the work is more than one LLM call
  AND the chaining is deterministic — e.g. `requirement_extraction` (docling-graph `auto/dense` is multi-call →
  extract → adapt), the ingestion/query composite subgraphs.

The split discipline (SKILL-SPLIT): when a former `function` bundled an LLM act with deterministic post-processing,
peel them — the LLM act becomes an `agent_skill` (its prompt authored into `SKILL.md`), the deterministic part
becomes a real `function`. Applied to `compliance_judgment`/`compliance_finding_assembly`,
`claim_extraction`/`claim_adaptation`, `extraction_semantic_judge`/`extraction_semantic_gate`,
`requirement_extraction` (→ subgraph) / `requirement_adaptation`, and `vision_to_text` (pure act, no peel).
**Every `agent_skill` keeps its prompt in its `SKILL.md`**, never a hardcoded string (`generation` was brought
into line for parity).

### 2. Agent-skill runtime tiers (how a skill is run) — the balanced approach

- **Single-shot act → the seam + `SKILL.md` method.** `build_structured` / `build_model` with the stripped
  `SKILL.md` body as the system/method prompt (the `*_method()` loaders: `judgment_method`, `transcription_method`,
  `generation_method`, ...). This is the correct, lightest runtime for a one-shot structured or text call. It is
  model-neutral (product = self-hosted Granite/Gemma via the profile seam, ADR-0039) and hermetically testable
  with an injected `structured_factory`.
- **Needs tools / subagents / memory → `create_agent` or a Deep Agent.** Reserve the heavier
  `create_agent + SkillsMiddleware` / Deep-Agent runtime for skills that genuinely need live tool use, subagent
  dispatch, or memory. **These already exist and are tested**: `rlm_chunking`/`rlm_synthesis` run as a real Deep
  Agent with granted subagents (ADR-0015/0017); `okf_navigate` runs as `create_agent` with its `SKILL.md` as the
  system prompt. They are the reference implementations.

**We do not build a `SkillsMiddleware` progressive-disclosure runtime speculatively.** Progressive disclosure
(an agent dynamically choosing which skill/asset to load) only earns its keep when a skill's assets/scripts must
be revealed on demand — no current skill needs that (each loads one self-contained method). That runtime gets
built **when a concrete skill needs asset/tool progressive disclosure**, using `okf_navigate`/`rlm` as the
pattern — not before (the no-speculative-code rule).

## Consequences

- The kind of every capability is now decidable from a written rule; the CI manifest tests pin kinds, so a future
  regression (an LLM-bearing `function`) fails a test, not a review.
- `agent_skill` prompts are authored artifacts under `skills/`, versioned and reviewable, not string constants —
  the same content can later be mounted into a live agent runtime unchanged.
- The deterministic guarantees a skill must NOT own (verdict vocab, citation validity, conservative defaults,
  AMBIGUOUS downgrades) live in the applying `function` / capability code, documented in each `SKILL.md`'s
  "what this skill does NOT own" section — the model reads, the code guarantees.
- Item "realize skill runtimes as LangGraph/DeepAgent" is closed as a **principle**, not open code: the heavy
  runtimes exist and are tested; the light path is correct for the rest; the middleware runtime is deferred to
  real need.
- Trade-off accepted: the light seam runtime does not exercise `SkillsMiddleware`, so the folder's non-`SKILL.md`
  assets (e.g. `template.py` schemas) are imported directly in Python rather than loaded through the skill system.
  That is fine for single-shot acts; it is the thing the deferred middleware runtime would change if a skill ever
  needs on-demand asset disclosure.
