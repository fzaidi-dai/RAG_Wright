# RuleWright handoff: clause granularity — the 0036 → 0039 arc (what a Clause is now)

Date: 2026-09-12 · on `origin/main` (commit `1049e22`) · ADR-0101 + ADR-0103 · **No API change. Fewer, better-bounded clauses; retrieval unchanged.**

This one handoff covers **issues 0036, 0038 and 0039 together** — they're one arc: 0036 removed a bad gate, which exposed a granularity problem (0038: a clause became a sentence), whose fix then needed two integration corrections (0039: keep docling's section-number marker, and depth-cap so nested list items fold). Read this as the current state; it supersedes the interim guidance that clause counts would rise to ~114.

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

## Integration fix so the numbers actually reach the detector (issue 0039)

You caught that a clean end-to-end run gave **15 provisions, not 71** (and stayed ~80% AMBIGUOUS). Root cause: **docling puts a numbered provision's number in a `marker` field and strips it from `.text`** (`marker="1.1."`, `text="…"`), and the chunker/segmenter/`starts_new_provision` all read `.text` — so the section numbers were invisible and every provision fell back to the chunk boundary. Your 71 (= the number of section markers) was the right target.

Fixed at the single reading-order view the chunker consumes (`content_items` → `_node_text`): an `enumerated` node with a `marker` is reconstructed as `"<marker> <text>"` (docling's `orig`), so the number is present where the detector looks.

**Confirmed by a clean re-ingest** (fresh docling parse of the INTERSECT PDF, all caches bypassed → `content_items` → segmentation → grouping). A robustness detail worth knowing: docling versions distribute the number differently — on the engine's installed docling, 73 of the sub-numbers were already **inline** in `text` and only **7** lived in `marker`; on your docling, all were in `marker` (which is why you got 15 pre-fix). The fix reconstructs the marker-only case while inline detection keeps working, so both docling versions now converge — it's version-robust, not tuned to one parse's quirk.

**Depth cap (your follow-up — you were right).** Restoring the number first gave **71**, which you correctly flagged as slightly too fine: a depth-blind rule reads `10.5.1.1.` as a section start, so nested list items became their own provisions — trading one granularity bug for a smaller one, against 0038's own "list items fold" rule. Fixed: the section detector is now **depth-capped to two levels** — `N.` or `N.N.` starts a provision; `N.N.N.` and deeper fold into their parent. Clean re-ingest after the cap: **65 provisions**, inside your defensible ~52–66 range. The remaining 65-vs-~52 gap is the one open judgement call you named — whether the **14 individually-numbered definitions** collapse under a single "1. Definitions" clause. I've **left them as individual provisions** (each defines its own term, arguably a provision); say the word and we collapse the definitions block to one clause to land ~52. That's a product call, not a detection question.

**Integrator note:** any parse path that supplies section numbers only via `marker`/`enumerated` (not inline in `text`) now works without change; a path that already keeps numbers inline is unaffected (no double-prepend). Reproduce deterministically (no model, no cache): fresh `parse()` → `content_items` → `segment_clause` → `clause_extraction_jobs`.

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

Reference: 0036 (ADR-0101), 0038 + 0039 (ADR-0103); `subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`, `spans/segment.py::starts_new_provision` / `is_extractable_span`, `corpus/document_parser.py::_node_text` (the 0039 marker fix). Full suite: 1560 passed. (Issue 0037 — carve-out/damage/subject verbatim retention — is a separate handoff.)
