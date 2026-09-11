# RuleWright handoff: a Clause is now a PROVISION, not a sentence (issue 0038)

Date: 2026-09-12 · on `origin/main` (commit `edd39ed`) · ADR-0103 · **No API change. Far fewer, better-bounded clauses.**

---

## Your diagnosis was exactly right

Removing the 0036 function gate left `is_extractable_span` (a furniture filter) deciding clause-hood, so a `Clause` became a sentence — 98% of spans, ~5.4× cost, a fragment-filled typed layer. The gate had been doing accidental provision detection. Fixed: **extraction is now per PROVISION; retrieval stays per span.**

## What changed

- **`clause_extraction_jobs` groups contiguous spans into provisions** and extracts once per provision (merged section text). A **provision boundary = a chunk change OR a heading span**, where a heading is: a numbered section (`2.1. …`) → else a bare Title-case heading → else a short ALL-CAPS heading (`spans.segment.starts_new_provision`, deterministic, no model).
- **Retrieval is unchanged** — every span is still embedded and indexed; only the *extraction unit* got coarser.
- **Furniture** spans are still dropped from a provision's merged text; an all-furniture provision produces no clause.

## Measured on your INTERSECT/Hovione contract

| unit | before | after |
|---|---|---|
| spans (retrieval) | 183 | 183 (unchanged) |
| **clause nodes** | **180** | **71** |
| extraction calls | 184 × 7 | 71 × 7 (~2.6× fewer) |

71, not the ~40–60 you estimated, because the definitions section (`1.1`–`1.x`) is individually numbered — each definition is its own provision. If you'd rather the definitions collapse under one "1. Definitions" clause, that's a tuning choice we can make; 71 real numbered provisions is already the right shape.

**Property precision recovers too.** Live extraction of whole provisions gives coherent, mostly-EXTRACTED properties (e.g. a Liability provision → mutuality/favorability/party_asymmetry/claim_scope EXTRACTED) at ~5:2–5:5 EXTRACTED:AMBIGUOUS — versus the 332:1122 (77% AMBIGUOUS) you saw when every fragment was a clause. The remaining AMBIGUOUS values are the issue-0037 verbatim retentions, not fragments.

## Graceful degradation (the "no numbered sections" case you asked about)

Granularity self-adjusts to the document:
- numbered sections → provision-level (~the section count)
- un-numbered but headed → provision-level (title/caps headings)
- **no headings at all → chunk-level** (the chunk boundary always breaks a provision)

The floor is per-chunk — **never per-sentence, never one clause per document.** A contract that genuinely has many provisions but *no* numbers and *no* headings would under-segment to chunk-level; that's rare, and the finer paragraph fallback is deferred until a corpus needs it (we didn't build speculative machinery).

## One identity change to be aware of (ADR-0025)

A provision-clause spans several sentences, so its citation is now **provision-level**: `clause_id` derives from the merged provision, and the clause anchors to its **heading/first span** (`span_id`). Retrieval still lands on any sentence-level span, and `contract_clause_index` / `contract_terms` return provision clauses as before. A non-anchor span doesn't individually carry the clause's properties via `span_properties` (it never carried a *different* clause's either); if a retrieval path of yours needs per-span property joins across a provision, tell us and we'll add multi-span linkage.

## This supersedes part of the 0036 handoff

The 0036 note said clause counts would *rise* (~31 → ~114) and cost with them. With 0038 the count settles at provision-level (~71 here) and cost drops ~2.6× from the per-sentence peak — while still recovering the provisions 0036 was about. Net vs the original pre-0036 state: more complete (untagged provisions included) AND better-bounded (whole provisions, not fragments).

Reference: commit `edd39ed`, ADR-0103, `spans/segment.py::starts_new_provision`, `subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`. Full suite: 1557 passed. Happy to run the property-precision A/B on your corpus — the clause-count probe is deterministic (`clause_extraction_jobs(segments)`), so you can reproduce the 71 exactly.
