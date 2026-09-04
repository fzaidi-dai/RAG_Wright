# ADR-0081: Function-independent thematic-group clause extraction (tag-parse), behind a seam flag

Date: 2026-09-05
Status: Accepted — **tagparse is now the ingestion DEFAULT** (broad A/B below); docling kept for rollback. Production model choice: stay on granite-4.2 for now.

Builds on ADR-0080 (nested tag-parse) and the finding recorded in ADR-0079 + memory
`function-classification-not-load-bearing`.

## Context

The docling-graph clause path fills the whole 35-field `Clause` template in ONE server-side-JSON call, which
granite-4.2 flattens/hard-fails on real clauses (~5/7 `PipelineError`). Two decompositions were considered:

- **Function-scoped** (ask only the function's applicable fields): RULED OUT — clause-function classification is
  not accurate enough to gate on (~0.37–0.50 top-1; a wrong function silently drops the right fields).
- **Function-INDEPENDENT**: split the schema by fixed theme, no classifier dependency. Chosen.

## Decision

Extract a `Clause` as several small **thematic tag-parse passes** over the *same* `Clause` schema
(`build_tag_structured(..., fields=<group>)`, ADR-0080), run concurrently and merged; `Clause`'s validators
normalize on the final construct. `CLAUSE_GROUPS` (8 groups: identity/scope, liability/damages,
temporal/termination, IP/licensing, consents/control, governing-law/dispute, restrictions/duties, exceptions)
covers every non-id field exactly once (coverage test enforced). Plugged into the existing
`DGClausePropertyExtractor` as the Clause-producing step, so the **same downstream applies unchanged**: adapt to
`ClausePropertyRecord`, ADR-0028 lexical grounding gate, ADR-0040 symbolic gate.

Two robustness pieces from the live A/B:
- **Value-sanity guard**: drop a string field value that is too long or contains XML tags — kills leaked
  chain-of-thought / prompt echo before it becomes a stored assertion (lexical grounding only *downgrades*, so it
  cannot remove this).
- **Aspect gate OFF by default**: a coarse recall-biased "which aspects apply?" pass exists (`RAG_INGEST_CLAUSE_GATE=1`)
  but granite UNDER-selects aspects (its conservative classification weakness), so gating drops groups and misses
  fields. Default = run all groups + rely on grounding; the gate is an opt-in cost lever for a stronger gate model.

Selected by `RAG_INGEST_CLAUSE_EXTRACTOR` (`docling` default | `tagparse`); the flag is for the A/B — tagparse is
NOT yet the ingestion default.

## Consequences

- **Structurally works**: tagparse extracts where docling hard-crashes (2/3 of the A/B clauses), garbage-guarded,
  grounding-gated. The shared **hint-format fix** (guidance moved OUTSIDE the tag body — an in-body `(hint)` was
  being echoed as the value and breaking enum parsing) and **field-subset** support benefit every tag-parse caller.
- **Per-field recall is the MODEL ceiling, not the design.** Live A/B (same architecture): gemma-4 recovered the
  3-year term and the full cap (basis + quantum + mutuality) that granite-4.2 missed. So production-grade recall
  needs gemma-4 or granite + multi-sample — the same cost/quality tradeoff as classification. Decision pending.
- **Known gap**: in the A/B both tagparse models missed a verbatim "Delaware" in the governing-law group
  (docling got it) — a specific group-path anomaly to debug next.
- No default behavior change (flag defaults to `docling`); full suite green.

## Broad grounded A/B + default flip (follow-up)

45 real CUAD clauses (15 functions × 3), scored on SUCCESS (record, no crash) and KEY-FIELD RECALL (a *grounded*
assertion on the gold function's *discriminative* dimension):

| config | success | key-field recall | grounded/clause |
|---|---|---|---|
| docling / granite | **0.11** | 0.09 | 1.2 |
| tagparse / granite | **1.00** | 0.47 | 1.6 |
| tagparse / gemma-4 | **1.00** | 0.56 | 1.6 |

- **docling hard-crashes ~89% of real CUAD clauses** → tagparse is now the DEFAULT (`RAG_INGEST_CLAUSE_EXTRACTOR`
  defaults to `tagparse`; docling remains for rollback). Cost note: tagparse is ~8 LLM calls/clause vs docling's
  ~1; the aspect gate is the cost lever pending a reliable gate model.
- **gemma only +0.09 over granite** → not worth ~10–20× cost; stay on granite-4.2, revisit with granite +
  multi-sample.
- **Both models are weak on the SAME functions** (License Grant, Cap On Liability, Warranty Disclaimer,
  Indirect/Consequential) → the residual (~0.5 recall) is NOT mainly model strength. Next work: check whether the
  ADR-0028 grounding gate over-drops open-valued fields (token-overlap on cap_quantum/covered_parties), then
  per-function prompt tightening (a future reusable clause-extraction skill).
