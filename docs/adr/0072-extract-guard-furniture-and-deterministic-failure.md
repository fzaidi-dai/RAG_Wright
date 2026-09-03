# ADR-0072: Clause-extraction guard — skip furniture, don't retry a deterministic failure

Date: 2026-09-04
Status: Accepted (implemented; EXTRACT-GUARD-1, bulk-ingestion wall reported by RuleWright)

A robustness backstop under ADR-0071 (de-fragmentation): once clauses are no longer shattered, the residual
non-clause spans (document furniture) must not hard-fail extraction, and a deterministic failure must not be
retried.

## Context

After DEFRAG-1 the NEONSYSTEMS wall was cleared, but two failure modes remained latent for other contracts: (a)
document **furniture** that a classifier still labels with a function — a page number (`9`), a signature/execution
label (`By: /s/ …`, `Name:`, `Title:`), a bare ALL-CAPS heading (`EXHIBIT C`, `AMENDMENT OF DEFINITIONS.`) —
carries no clause properties, so docling-graph returns "No valid JSON" and the pipeline reports it as a lost
clause; and (b) that failure is **deterministic** (the same furniture text re-fails identically), yet the ingest
retried it 3× (`_CLAUSE_EXTRACT_ATTEMPTS`), burning minutes.

## Decision

1. **A recall-first extraction guard.** `is_extractable_span(text)` (in `spans/segment.py`) returns `True` for any
   span carrying lowercase prose (a real provision has function words), and declines only clear furniture:
   near-empty spans (`< 6` alphabetic chars), short bare ALL-CAPS labels/headings (`< 6` words), and
   signature/execution-block label lines (`^(by|name|title|attest|witness|its|date|signature)\s*:` — universal,
   domain-neutral, with the trailing colon so a provision that merely starts with the word does not match). A long
   ALL-CAPS provision (a capitalised disclaimer, `>= 6` words) is still extracted. `clauses_fn` filters typed
   spans through it before extraction.

   A skipped span is **not** a content loss: `index_spans` runs on a separate branch, so the span stays in the
   retrieval index; the guard only declines to mint a clause-KG node from furniture. The predicate errs toward
   extracting (recall-first), so a real short clause is never dropped by it.

2. **No retry on a deterministic failure.** `clauses_fn` catches `ExtractionFailed` (docling-graph's "No valid
   JSON") separately and records it **once** instead of retrying 3×; genuine transient errors (timeout / network)
   still retry. A substantive span that deterministically fails is still recorded as a failure — no-silent-loss is
   preserved; only the wasteful retry of a deterministic outcome is removed.

## Consequences

- **Full end-to-end verified (PARSE-1 + DEFRAG-1 + EXTRACT-GUARD-1, real ArcadeDB + granite).** NEONSYSTEMS:
  **~11 min → 100s**, **PARTIAL (6–10 lost) → INGESTED clean**, **24+ failures → 0**. 45 real clauses written
  (furniture filtered from 56 typed spans), 96 spans indexed. RuleWright's independent wider-sample run confirmed
  the JSON-failure class is gone (56 → 0).
- **No content loss.** A guarded furniture span remains retrievable via the span index; only its (non-existent)
  clause properties are declined.
- **Domain-neutral.** The furniture signals (page numbers, ALL-CAPS labels, By/Name/Title/Attest/Witness/Its/Date
  signature lines) are universal contract-execution furniture, not customer-specific knowledge.
- **No API/identifier/schema change.** `is_extractable_span` is a pure predicate; the guard and the retry change
  are internal to `clauses_fn`.
- Closes 3 of the 4 bulk-ingestion-wall tasks; TAGPARSE-INGEST-1 remains a backlog item. (A separate, newly
  surfaced issue — large born-digital documents hitting the 600s parse deadline because the fast tier still OCRs
  born-digital pages — is tracked next; it is a parsing-speed defect, not an extraction one.)
