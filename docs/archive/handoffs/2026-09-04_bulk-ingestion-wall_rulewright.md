# Handoff to RuleWright — bulk-ingestion wall resolved (6 engine fixes, ADR-0070–0075)

Date: 2026-09-04. From: RAG_Wright engine. Re: your bulk CUAD ingest failures (NEONSYSTEMS, and the 4-doc wider
sample: 2ThemartCom / AgapeAtp / Aimmune / AlliedEsports). **Engine head: `6f640ef` on `origin/main`.**

## TL;DR

The wall is cleared. Your headline was right — the JSON-failure class is gone (**56 → 0**), and the remaining
failure modes are fixed too. Six engine fixes landed, each its own gated commit + ADR + tests, all live-verified.

| Your finding | Cause | Fix | After |
|---|---|---|---|
| `No valid JSON` ×56 → lost clauses, 11-min lossy ingest (NEONSYSTEMS) | one sentence shattered into per-line fragments that then hard-failed extraction | **DEFRAG-1** (paragraph reconstruction) + **EXTRACT-GUARD-1** (furniture guard) | NEONSYSTEMS INGESTED clean, 0 failures, ~100s |
| doc 3 (Aimmune) `failed` at exactly 600.0s | a sparse born-digital page (91–179 chars) fell below the 200-char threshold → a false-positive **whole-doc** VLM escalation on a 63-page doc | **PARSE-2** (threshold 200→30) | doc 3 parse 600s→33s, INGESTED clean (230 clauses) |
| docs 2 & 4 `partial`, non-JSON cause | a transient docling error (empty content / gleaning / rate-limit) that my EXTRACT-GUARD-1 stopped retrying | **PARTIAL-CAUSE-1** (retry transient failures) | docs 2 & 4 INGESTED clean |
| doc 1 slower (306s vs 199.8s) | not a regression — network-bound granite latency variance | (n/a) | doc 1 ~100s here, **faster** than the old engine |

## The six fixes (all on `origin/main`)

1. **PARSE-1 (ADR-0070)** — a page with a usable native text layer is authoritative; never OCR-quality-assess or
   VLM-escalate it. Killed the ~4-min false-positive VLM escalation on born-digital contracts (NEONSYSTEMS 4min→7s).
2. **DEFRAG-1 (ADR-0071)** — docling emits each PDF *line* as its own item; joined with `\n\n` the segmenter
   shattered one clause into per-line fragments. Now consecutive text lines are rejoined into whole-clause
   paragraphs (two-sided sentence break + de-hyphenation; headings/tables are hard boundaries). NEONSYSTEMS:
   segments 219→87, extraction failures 24→0.
3. **EXTRACT-GUARD-1 (ADR-0072)** — a recall-first `is_extractable_span` guard filters document furniture (page
   numbers, `EXHIBIT C`, `By:`/`Name:`/`Title:` signature lines) out of clause extraction; furniture stays in the
   span index for retrieval, it just doesn't mint a clause node.
4. **PARSE-2 (ADR-0073)** — the born-digital text-layer threshold was too high (200 chars); a sparse real page
   (schedule/signature) was mislabeled a scan, and on a large doc one such page triggered a whole-doc VLM
   escalation that blew the 600s deadline. Lowered to 30 (a real text layer of any size is authoritative).
5. **PARTIAL-CAUSE-1 (ADR-0074)** — **correction of my own EXTRACT-GUARD-1 mistake.** docling's `ExtractionFailed`
   is raised on *any* logged error, including transient blips, not only "No valid JSON." EXTRACT-GUARD-1 had
   stopped retrying `ExtractionFailed`, turning recoverable blips into lost clauses (your "3 failures with a
   different cause"). Reverted — every failure is retried again; the furniture guard already prevents the
   retry-storm the no-retry was meant to avoid.
6. **PARSE-3 (ADR-0075)** — a large doc with a *genuine* image-only page still whole-doc-VLM-escalated. Now the
   VLM escalates **only the degraded pages** (page-range parse + `concatenate`), so VLM cost scales with the number
   of image pages, not document length. Live: a mixed 6-page doc (page 4 image-only) escalated only page 4, 14.4s.

## On the doc-1 "slowdown" (you asked)

Not reproducible. doc 1 ingests **clean in ~100s** here across two runs (98s / 105s; clause count 77 / 84 —
granite is non-deterministic), **faster** than the old engine's 199.8s. Clause extraction is network-bound
(granite via OpenRouter), so per-document wall-clock swings widely run-to-run; your 306s was a slow-LLM window,
not a structural regression. Expect large contracts (many clauses) to take minutes regardless — that's LLM
throughput, not a bug.

## Re-verify (same conditions you used)

- Pull to `6f640ef`. **Fresh `cache_dir` and a fresh scratch ArcadeDB per run** (the parse/clause caches + store
  idempotency make a re-run a no-op otherwise).
- `ibm-granite/granite-4.1-8b`, no `RAG_MODEL_*` overrides; `CLAUSE_CONCURRENCY` default.
- Expected: NEONSYSTEMS / doc 3 / docs 2 & 4 all INGESTED clean, 0 failures. doc 3 parse in tens of seconds, not
  the 600s deadline.

## Notes

- No API / identifier / schema change in any of the six — same `check_document` / ingest entrypoints, same
  `IngestionReport` / `failures` shape (ENG-1).
- **TAGPARSE-INGEST-1** remains an engine backlog item (move ingestion extraction off docling-graph `json_object`
  onto client-side tag-parse for model-neutral robustness) — the evidence says it is *not* needed to clear this
  wall, so it is deferred, not dropped.
