# ADR-0022: FR-K, an embedding-free OKF knowledge-navigation retrieval path (experimental, gated)

Date: 2026-07-22. Status: Accepted (spec + task-ledger authoring; no capability code built yet). Adds a new
experimental capability block FR-K to `SPEC.md` and tasks T45-T53 + GATE-3a/GATE-3 to `tasks.md`. Derived
from an external feeder draft (`temp/okf_spec_delta_and_tasks.md`) reworked in review.

## Context

The T41 retrieval investigation diagnosed the ACORD recall shortfall (two-leg recall@50 0.379 against a
grounded bar of 0.667) as a **query-representation gap**: query-to-relevant cosine 0.534, below the
random-pair baseline 0.561, while clause-clause structure is strong (reachability without rankability).
Every lever in the current stack routes through that broken signal: dense retrieval ranks by it, RRF fuses
lists produced by it, and the cross-encoder reranker was measured as neutral because it filters noise but
cannot promote query-distant relevants.

Google's Open Knowledge Format (OKF, Apache 2.0) represents knowledge as a directory tree of markdown files
with YAML frontmatter, cross-linked by standard markdown links, with `index.md` progressive-disclosure
listings. It suggests a retrieval mechanism that never computes a query-to-clause similarity: compile the
chunk corpus into an OKF bundle and **navigate** it (a model reads signposts and decides) rather than
**rank** it. That is the same escape hatch as the ACORD-indicated LLM-reranker lever, applied at the
navigation stage, and a close relative of the category label-retrieval item already in the T41 backlog.

We already hold the pieces this needs: full chunk text as a sidecar (T40), concurrent chunk summaries
(T-SUM), the RLM dynamic-sub-agent machinery (T15/T36/T37), and the per-process interpreter serialization
floor (T35, ADR-0020).

## Decision

Add **FR-K**, a general, corpus-neutral, embedding-free retrieval path, as an **experimental, gated**
capability block. Compile side (`okf_compile`, FR-K.1-K.4) and traversal side (`okf_navigate`, FR-K.6),
each built and registered as ordinary software; the graphs that sequence them are compiled by GraphWright,
not built here. Key decisions:

1. **It is never a default.** The hybrid path stays primary. FR-K is a registered alternative retrieval
   path a query graph may bind.
2. **Corpus-neutral, per-corpus enablement.** FR-K is a governed capability of the system, not an ACORD
   one-off. ACORD is the first validation corpus. Whether the path is enabled for a given corpus is decided
   by a cheap, model-free **reachability spike** (FR-K.8, GATE-3a) run before any traversal model call is
   spent. This matches the product frame: a new corpus means a new eval set and re-orchestration, not new
   capabilities.
3. **Categories are manufactured via `graph_extraction`.** ACORD ships no corpus-side clause taxonomy
   (BEIR corpus is `{_id, text}`), so the category signpost that drives the directory tree and tags is
   produced by classifying each clause through `graph_extraction` (FR-C.6). Its coverage and confidence are
   a reported number, because that quality is both the retrieval ceiling and the control-arm mechanism.
4. **Index descriptions reuse existing chunk summaries** (T-SUM), not a second model pass.
5. **Reachability first, as a formal kill-switch.** The model-free reachability analyzer and the
   category-label control run before the traversal capability is built. GATE-3a reads the ceiling: if gold
   is not signpost-reachable within bounds, or the coarse-label control already captures the lift, the
   experiment is redirected for the cost of a compile and an analyzer. This is the first execution slice
   (T45-T48); the rest of the program is specified now but built behind the gate.
6. **The category-label control is the honest baseline.** GATE-3 judges OKF traversal against the
   category-label control (T48), not against the 0.379 two-leg baseline it will trivially differ from.
7. **No harness-profile seam.** The feeder draft proposed a separate harness-profile seam; dropped. The
   traversal binds its model through the existing T11 model-profile seam, and depth/frontier bounds and the
   PTC allowlist are ordinary run configuration recorded with each result.
8. **The bundle is a gitignored, rebuildable data artifact**, recipe-stamped so any reachability number is
   attributable to a specific compile recipe. `chunk_id` identity is unchanged across a compile (no
   re-chunk), so the link between a chunk and its graph nodes is preserved (FR-S.2).

ARD scope (the section-5 three-category rule): `okf_compile` is category 3 (foundation derivation, canonical
slug, no manifest, same shape as `ontology_registry_derivation`); `okf_navigate` is category 1
(query-discovered, canonical slug and ARD manifest). FR-K.5 (navigation primitives) registers nothing;
FR-K.8 (reachability) and FR-K.9 (strategy memory) are evaluation and infrastructure software.

## Consequences

- SPEC.md gains the FR-K block, assumption 5, the section-3.1 in-scope note, Phase 5 with GATE-3a/GATE-3,
  boundary lines, open questions 11-15, and glossary entries. tasks.md gains T45-T53 + the two gates, detail
  entries, and coverage rows. No capability code is built by this ADR; the working loop gates each task.
- The OKF reference repo is cloned to `/Users/farhan/work/knowledge-catalog/okf` and graphify-indexed at
  `src/graphify-out/graph.json` for grounding, per the playbook. The reference agent is Google ADK + Gemini
  + BigQuery, not our stack, so the compiler is reimplemented on langchain_openai/deepagents; only the
  format logic (`OKFDocument`, `regenerate_indexes`, `concept_id_to_path`) is mirrored.
- **Known risk, measured not assumed:** `graph_extraction`'s categories are the 41 CUAD-derived ontology
  categories (T4/T23); classifying ACORD clauses into that taxonomy is an assumption of fit. T46 reports
  category coverage/confidence so a poor fit surfaces as a number, not a silent low ceiling. Because the
  control arm runs on the same manufactured labels, control and compile share one point of failure; stated,
  and bounded by the reported quality.
- **Task-ID collision fixed:** the feeder draft numbered its tasks T43-T52, colliding with the already
  committed T43/T44 (`capabilityInterface`). Renumbered to T45-T53; GATE-3 and the FR-K namespace were both
  verified free.
- If FR-K is removed at GATE-3, the reachability instrument, the trace harness, and the ACORD gold labels
  are kept regardless, since they are useful to any retrieval work.
