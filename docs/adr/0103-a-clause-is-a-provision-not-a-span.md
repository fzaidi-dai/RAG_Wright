# ADR-0103: a Clause is a PROVISION (numbered section), not a span

**Status:** accepted · **Date:** 2026-09-12 · **Resolves:** engine issue 0038 · **Follows:** ADR-0101 (issue 0036, removed the function gate) · **Related:** ADR-0025 (clause↔span identity / small-to-big), ADR-0082 (function is never an ingest gate)

## Context

Issue 0036 correctly removed the ~0.37-accuracy function gate from clause creation. But that gate had been doing *accidental provision detection* (a span tagged "Confidentiality" ≈ "this is a provision"), and removing it left `is_extractable_span` — a **furniture filter, not a provision detector** — as the only thing between a span and a clause. Result: a `Clause` became a **sentence**. On the INTERSECT/Hovione contract, 180 of 183 spans (98%) became clause nodes; ingest cost rose ~5.4×; the typed layer filled with fragments (a lead-in like `(i) the Product Specifications;` has no properties, so seven thematic questions of it only produce noise — RuleWright saw 77% AMBIGUOUS assertions).

The retrieval layer is deliberately sentence-level (spans, ADR-0025 small-to-big). The **extraction** layer should be coarser: a provision. Measured on the same contract: chunks = 10 (too coarse), spans = 184 (too fine), **numbered-section headings = 54** (≈ the real provision count).

## Decision

**Extract one clause per PROVISION; keep retrieval per span.**

- `spans.segment.starts_new_provision(text)` — tiered, deterministic provision-start detector: a numbered-section enumerator (`2.1. …`), else a bare Title-case heading, else a short ALL-CAPS heading.
- `contract_ingestion_pipeline.clause_extraction_jobs(segments)` now **groups** contiguous segments into provisions and emits one job per provision `(index, anchor_op, function, scores, merged_text)`. A **provision boundary is a chunk change OR a heading span**. Within a provision, `is_extractable_span` still drops furniture spans from the merged text; an all-furniture provision yields no clause.
- `index_fn` is unchanged — every span is still embedded and indexed for retrieval.
- **Graceful degradation** (the key property): granularity self-adjusts — numbered sections → provision-level; an un-numbered document → **chunk-level**, with a *standalone* heading (a Title-case or ALL-CAPS span with no terminal punctuation, e.g. `Governing Law`) splitting further where one survives segmentation. A heading folded into its body, or a one-word heading with a trailing period (`Confidentiality.`), is NOT a boundary → it stays chunk-level. The chunk boundary always breaks, so the worst case is per-chunk, **never per-sentence and never one-clause-per-document**. Empirically verified across 120 CUAD contracts: the un-numbered ones land at ~chunk-level (e.g. 258 spans/15 chunks → 23 provisions; 278/16 → 24; 54 spans/1 chunk → 3) — never per-sentence. The finer paragraph fallback is deferred (not built) until a corpus needs it.

## Clause↔span identity (ADR-0025 change)

A provision-clause spans several sentences, and the tag-parse extractor reads the merged text, so it cannot attribute a property to one sentence. So the clause anchors to its provision:
- `clause_id` derives from the merged provision text (content-hash gate intact).
- `span_id` = the provision's **anchor** (first / heading) span — provision-level citation. Retrieval still lands on any sentence-level span; the property layer is cited at the provision.

## Consequences

- **Fewer, better-bounded clauses.** Live on the INTERSECT contract: 184 → **71 provisions** (the extra ~15 over the 40–60 estimate are the individually-numbered `1.x` definitions, each legitimately a provision). ~2.6× fewer extraction calls, same factor off cost.
- **Higher property precision.** Whole provisions extract coherent, mostly-EXTRACTED typed properties (live: 5:2 and 5:5 EXTRACTED:AMBIGUOUS) instead of fragment noise (the reported 332:1122). The AMBIGUOUS tail is now the 0037 verbatim retentions, not fragments.
- Provision-level citation is coarser than sentence-level; a non-anchor span does not individually carry the clause's properties (it never carried a *different* clause's either). Multi-span linkage is a possible future refinement if a retrieval path needs per-span property joins.
- Depends only on deterministic structure (chunk boundaries + heading regex); no model, reproducible.

Full suite: 1557 passed, 44 skipped.

## Follow-up: the detector must read the docling marker (issue 0039)

Integration defect found by RuleWright running 0038 end to end: docling emits a numbered provision as an **enumerated list item** with the number in a `marker` field and STRIPPED from `.text` (`marker="1.1."`, `text="…"`). The chunker/segmenter/`starts_new_provision` all read the text, so on a properly-numbered contract the detector saw almost no section numbers and every provision fell back to the chunk boundary — 15 clauses where the (cached, number-retaining) measurement gave 71, and property precision stayed ~80% AMBIGUOUS (a whole chunk of unrelated provisions is the coarse-side failure of the same "wrong granularity → noise" argument).

Fix (`corpus/document_parser._node_text`, used by `content_items` — the single reading-order view the chunker consumes): for an `enumerated` node with a `marker`, reconstruct `"<marker> <text>"` (docling's own `orig`), so the section number is present exactly where the detector looks. Bullet/letter markers are restored too but don't trip the numeric section detector (they fold into their provision). Verified end to end (deterministic, no model): a 6-marker enumerated doc → 6 provisions; the cached older-schema parses (no `marker`) are unaffected. Full suite: 1560 passed.
