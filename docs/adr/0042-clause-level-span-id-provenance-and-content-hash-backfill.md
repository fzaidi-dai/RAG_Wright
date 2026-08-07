# ADR-0042: Persist the clause-level span_id, and backfill it by content-hash join

Date: 2026-08-07
Status: Accepted

## Context

A registered query leg (`intra_document_qa`) abstained on real data (Modal KG, MCP-PROTO Phase A). Root cause:
a **property-less clause** — a clause the extractor tagged with a function but from which it pulled no typed
properties — reached the answer generator as a **bare function label** ("Uncapped Liability") with no citable
text, and the generator correctly refused to answer on contradictory, unsupported evidence.

The clause↔text link ran only *through the properties*: `PropertyAssertion.span_id` carried the operative span
(ADR-0025), but `ClausePropertyRecord` had **no clause-level span_id**. So a property-less clause had no span
link at all — even though, at ingest, each clause is extracted from exactly **one** operative span and the
extractor already **knew** that `op.span_id` (`clause_extractor(..., span_id=op.span_id)`). We were discarding a
1:1 provenance link we already had.

Two query-time patches were rejected:
- **Drop** the contentless clause — safe but lossy.
- **Rehydrate by function label** — a clause's function maps to *many* spans, so attaching "a" same-function
  span risks a confident **mis-citation** (worse than silence for a legal/compliance product).

## Decision

Persist the clause's operative span_id as first-class provenance, and rehydrate a property-less clause from
**its own** span (1:1) — never a function-label guess.

1. **Data model** — add `span_id` to `ClausePropertyRecord`, populated from the known `op.span_id` at extraction
   (`clause_to_record`); add `Clause.span_id` to the store schema; carry it on the `Clause` vertex UPSERT; select
   it in `clauses_in_contract`; expose it on the query-side `CitedClause`.
2. **Rehydration** (`rehydrate_clause_texts`, extracted from `intra_document_qa` for testability): a clause WITH
   properties uses its property span_ids (grounding invariant: they MUST resolve, else KeyError); a PROPERTY-LESS
   clause uses its OWN `span_id`; a clause with no span link at all (legacy) is omitted and cites its function
   label. `relational_qa` shares the same evidence discipline.
3. **Backfill for existing KGs** (`scripts/backfill_clause_span_id.py`) — **in-place, non-destructive,
   deterministic, no LLM**. The join key exists by construction: `clause_id` = `<source>:<index>:<sha256(op.text)>`
   and the Span index stores that same **raw** `op.text` (`to_span_record`, unstripped), so
   `sha256(span.text) == the hash embedded in clause_id`. For each clause, match to its span by that hash and
   `UPDATE Clause SET span_id`. Idempotent (skips already-correct rows); `DRY_RUN=1` reports the match rate. The
   store is env-selected, so the same script runs against the local KG or the Modal KG (a live writable https
   service) in place — no re-ingest, no backup/restore, touching only the new `Clause.span_id` field.

## Consequences

- Property-less clauses now carry **reliable, one-to-one** citation text. No drop, no mis-citation.
- Matching on the **content hash of the text** makes even the rare identical-text edge case faithful (the cited
  text is correct regardless of which span_id). An unmatched clause (a best-effort span-write that failed at
  ingest) keeps `span_id=""` and is left as-is — safe.
- The backfill is a **column-fill migration**, not a rebuild: spans, typed property edges, entities, and PARTY_TO
  links are untouched, on both the local and Modal KGs. It requires the KG to be *running* to receive the
  UPDATEs (you cannot update a stopped DB); the DB is never destroyed. The 506-vs-510 local/Modal divergence is
  unaffected — each KG's own clauses are updated independently.
- Caveat carried until run: the **actual match rate** on real data is confirmed by a `DRY_RUN` pass before
  applying (structurally ~100%, minus any best-effort span-write failures at ingest).
- New ingests carry the clause span_id natively; only pre-existing KGs need the one-time backfill.
