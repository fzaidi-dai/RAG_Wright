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
- **Graceful degradation** (the key property): granularity self-adjusts to the document — numbered sections → provision-level; a heading-less document → chunk-level (the chunk boundary always breaks). The worst case is per-chunk, **never per-sentence and never one-clause-per-document**. A no-number-no-heading contract under-segments to chunk-level; the finer paragraph fallback is deferred (not built) until a corpus needs it.

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
