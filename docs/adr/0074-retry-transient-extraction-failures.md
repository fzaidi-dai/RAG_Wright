# ADR-0074: Retry a docling ExtractionFailed — it captures transient errors, not only deterministic ones

Date: 2026-09-04
Status: Accepted (implemented; PARTIAL-CAUSE-1, bulk-ingestion wall reported by RuleWright)

Corrects the retry decision in **ADR-0072** (EXTRACT-GUARD-1). The furniture guard from ADR-0072 stands; the
"don't retry `ExtractionFailed`" half of it was wrong and is reverted here.

## Context

RuleWright's wider-sample run (which had DEFRAG-1 + EXTRACT-GUARD-1) showed the "No valid JSON" class gone (56 →
0) but left 2 of 4 documents `partial` with "3 extraction-stage failures with a different cause." Reproduction:
docs 2 (AgapeAtp) and 4 (AlliedEsports) ingest **clean (0 failures)** here, and neither VLM-escalates at any
threshold — so the partials are non-deterministic, not structural.

Root cause (in code): `dg_extraction.capture_docling_errors` collects **every ERROR-level** record docling-graph
logs during a call, and `extract_parties`/`extract_clause` raise `ExtractionFailed` whenever that list is
non-empty. That set includes deterministic "No valid JSON" **and** transient blips — an LLM empty response, a
gleaning failure, a provider rate-limit, a socket timeout. EXTRACT-GUARD-1 added `except ExtractionFailed: break`
(record once, no retry) on the theory that `ExtractionFailed` was deterministic furniture. It is not: on a
substantive clause a transient blip raises the same `ExtractionFailed`, and skipping the retry turns a recoverable
call into a lost clause. RuleWright hit 3 such transients; this run hit 0 — the signature of transient failures.

## Decision

Retry every clause-extraction failure again (revert the ADR-0072 no-retry). The bounded retry (3 attempts) is what
recovers a transient docling/LLM error. The **furniture guard stays** (`is_extractable_span` filters non-clauses
*before* extraction), so the retry loop no longer storms on deterministic furniture — the concern the no-retry was
meant to address is already handled upstream. The retry logic is extracted into a module-level
`_aextract_clause_with_retry(extractor, …, attempts)` so the behavior is unit-testable; the now-unused
`ExtractionFailed` import is removed from the pipeline.

## Consequences

- **Transient failures recover, no-silent-loss preserved.** A transient `ExtractionFailed` (empty content /
  gleaning / rate-limit / timeout) is retried and recovers; a persistent failure is still recorded after
  exhausting retries (flags the document PARTIAL — never a silent drop). Unit-tested both ways.
- **No furniture retry-storm.** ADR-0072's guard removes furniture before extraction, so retrying substantive
  spans does not reintroduce the storm the no-retry was meant to prevent.
- **Docs 2 & 4 clean; NEONSYSTEMS unchanged.** Live: docs 2/4 ingest clean; NEONSYSTEMS e2e still INGESTED clean
  (45 clauses, 96 spans, 0 failures). Full suite 1416 pass (+2 hermetic).
- **Supersedes** the no-retry paragraph of ADR-0072 only; the `is_extractable_span` guard and the deterministic-vs-
  transient framing there are otherwise unchanged.
- **No API/identifier/schema change** — internal to `clauses_fn` plus a testable helper.
