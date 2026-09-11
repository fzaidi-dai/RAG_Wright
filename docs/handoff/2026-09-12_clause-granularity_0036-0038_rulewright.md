# RuleWright handoff: clause granularity — the 0036 → 0038 arc (what a Clause is now)

Date: 2026-09-12 · on `origin/main` (commit `779586a`) · ADR-0101 + ADR-0103 · **No API change. Fewer, better-bounded clauses; retrieval unchanged.**

This one handoff covers **issues 0036 and 0038 together** — they're the same arc: 0036 removed a bad gate, which exposed a granularity problem, which 0038 fixed. Read this as the current state; it supersedes the interim guidance that clause counts would rise to ~114.

---

## The arc in one paragraph

`clauses_fn` gated clause creation on a ~0.37-accuracy soft function tag (`canonical_function(...) is not None`), silently dropping every untagged prose span — a coin-flip was setting the *size* of the typed layer (**0036**, your finding, correct). We removed the gate. But that gate had been doing *accidental provision detection*, and removing it left `is_extractable_span` — a furniture filter, not a provision detector — deciding clause-hood, so a `Clause` became a **sentence**: 98% of spans became clauses, ~5.4× cost, a fragment-filled typed layer (**0038**, also your finding, correct). The fix: **a `Clause` is a PROVISION** (a numbered section), extracted once; **retrieval stays per span**.

## Where it landed (INTERSECT/Hovione contract)

| | pre-0036 | after 0036 (per-sentence) | **after 0038 (per-provision)** |
|---|---|---|---|
| spans (retrieval) | 183 | 183 | 183 (unchanged) |
| **clause nodes** | 29 | 180 | **71** |
| extraction calls | ~29×7 | 180×7 | **71×7 (~2.6× off the peak)** |

71 (not the ~40–60 you estimated) because the definitions section (`1.1`–`1.x`) is individually numbered — each definition is its own provision. Collapsible under one "1. Definitions" clause if you'd prefer; 71 real numbered provisions is already the right shape.

**Net vs the original pre-0036 state: more complete AND better-bounded** — untagged provisions are now included (0036), and clauses are whole provisions rather than fragments (0038).

## The 0038 fix, stated plainly (no ambiguity)

**One `Clause` node = one provision.** A provision is a maximal run of consecutive spans, in document order, from one chunk, bounded by the next heading span. Concretely:

- **Grouping.** Walk the ordered spans. Start a new provision at a **chunk change** OR a **heading span**; every following span (continuation sentences, `(i)`/`(a)` list items) folds into the current provision. Furniture spans (`is_extractable_span` == False) are dropped from the merged text; a provision that is all furniture produces **no** clause.
- **Extraction.** The extractor runs **once per provision**, on the **concatenation of that provision's kept spans** (heading + its sentences + list items), producing one `Clause`. It never sees a lone sentence any more.
- **`clause_id`** is the content hash of the merged provision text (so the content-hash idempotency gate still holds).
- **`function`** on the provision clause = the **first non-NONE** classifier label among its spans, else `NONE`. Function remains a soft tag, never a gate — a `NONE` provision is still extracted. (If you scope queries by `clause.function`, note it is now the provision's representative label, not a per-sentence label.)
- **`span_id`** = the provision's **anchor span** (its heading / first span) — the citation anchor.
- **Retrieval is untouched:** every original sentence-level span is still embedded and indexed; only the *extraction* unit changed.

That is the whole change. Everything below is consequence.

## What changed, concretely

1. **Function is never a clause gate** (0036 / ADR-0101). `is_extractable_span` handles furniture only; an untagged provision still becomes a clause (function-independent extraction). We also **removed the aspect gate** (`RAG_INGEST_CLAUSE_GATE`) — an A/B measured it dropping ~18% of properties (carve-outs), so it was dead config.
2. **A Clause is a provision** (0038 / ADR-0103) — see "The 0038 fix, stated plainly" above for the exact grouping/extraction/id rules. The heading detector (`spans.segment.starts_new_provision`) is deterministic and model-free: numbered section → standalone Title heading → short ALL-CAPS heading.
3. **Retrieval is unchanged** — every span is still embedded and indexed (`index_fn` untouched). Only the *extraction unit* got coarser.

## Property precision recovers

Extracting whole provisions gives coherent, **mostly-EXTRACTED** properties (live: a Liability provision → mutuality/favorability/party_asymmetry/claim_scope EXTRACTED; ~5:2–5:5 EXTRACTED:AMBIGUOUS) — versus the **332:1122 (77% AMBIGUOUS)** you measured when every fragment was a clause. The remaining AMBIGUOUS values are the issue-0037 verbatim retentions (carve-out/subject/damage tail kept, not lost), not fragments.

## Graceful degradation — the "no numbered sections?" case, verified

Granularity self-adjusts:
- numbered sections → provision-level (~the section count)
- un-numbered → **chunk-level**, with a *standalone* heading (Title-case/ALL-CAPS, no trailing period, e.g. `Governing Law`) splitting further where segmentation preserves one. A folded heading, or a one-word heading with a period (`Confidentiality.`), stays chunk-level.
- no headings at all → **chunk-level** (the chunk boundary always breaks a provision).

The floor is per-chunk — **never per-sentence, never one clause per document.** Verified across 120 CUAD contracts: the un-numbered ones land at ~chunk-level (**258 spans/15 chunks → 23 provisions**; 278/16 → 24; 54 spans/1 chunk → 3). A contract with many provisions but *no* numbers and *no* headings under-segments to chunk-level; rare, and the finer paragraph fallback is deferred until a corpus needs it.

## Two things to be aware of

- **Clause↔span citation is now provision-level** (ADR-0025 change): `clause_id` derives from the merged provision, and a clause anchors to its **heading/first span**. Retrieval still lands on any sentence-level span, and `contract_clause_index` / `contract_terms` return provision clauses as before. A non-anchor span doesn't individually carry the clause's properties via `span_properties` (it never carried a *different* clause's either); if a retrieval path of yours needs per-span property joins across a provision, tell us and we'll add multi-span linkage.
- **Cost lever** is the provision unit itself (2.6× off the per-sentence peak). Group pruning and two-clause batching were both measured at a ~15–18% recall loss and rejected; if cost is still binding, the levers are a stronger/cheaper served model or tightening `is_extractable_span`, not fewer calls per clause.

## Reproduce
The clause-count probe is deterministic (no model): `clause_extraction_jobs(segments)` over a document's segments returns the provisions — you can reproduce the 71 exactly, and the degradation numbers on any contract.

Reference: commits `6b69ab4` (0036) + `edd39ed`/`779586a` (0038), ADR-0101 + ADR-0103, `subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`, `spans/segment.py::starts_new_provision` / `is_extractable_span`. Full suite: 1557 passed. (Issue 0037 — carve-out/damage/subject verbatim retention — is a separate handoff.)
