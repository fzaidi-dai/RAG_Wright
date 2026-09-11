# RuleWright handoff: issue 0036 resolved — untagged provisions now reach the clause KG

Date: 2026-09-12 · on `origin/main` (commit `6b69ab4`) · ADR-0101 · **No API change. This changes clause-KG SIZE (up), not any signature.**

---

## Your finding was right, and it's fixed

`clauses_fn` gated clause creation on `canonical_function(function) is not None` — so an untagged prose span (`function=NONE`) never reached the extractor, and a ~0.37-accuracy soft tag was setting the *size* of the typed layer. That contradicted ADR-0082 ("function is never an ingest gate") and was unintended. Now **`is_extractable_span` alone decides**; an untagged-but-extractable span is extracted with `function=NONE` and its typed properties land like any other's.

Expect your clause counts to rise substantially (your own measurement: ~31 → ~114 on the INTERSECT/Hovione contract). Those recovered clauses carry real properties (`carve_out`, `cap_basis`, `temporal_bound`, …) and reach you through `contract_terms` / `contract_clause_index` exactly as typed clauses — verified. Some spans that are prose-but-not-a-provision (e.g. a recital) will extract *thin* (few/no assertions); consumers that filter to clauses-with-properties (your `party_exposure`) already exclude those.

## Three changes (all shipped together)

1. **Untagged spans reach extraction** (the fix above).
2. **`is_extractable_span` tightened** — two deterministic, high-precision furniture rules now decline more true non-provisions: **TOC dotted-leader lines** (`… .......... 12`) and **notice-block contact labels** (`Attention:`/`Attn:`/`Fax:`/`Facsimile:`/`Email:`/`Telephone:`). Still recall-first: a notice *clause* ("…sent to the following address:"), a telephone-support provision, and section decimals (`Section 3.1`) stay extractable. A dropped span still lives in the span index for retrieval — it's only declined a clause node.
3. **The aspect gate is removed** (`RAG_INGEST_CLAUSE_GATE` is gone). We A/B'd it on Qwen (your default model) over 24 real clauses: it dropped **~18% of properties** (recall 0.817), concentrated in **`excepts` — carve-outs** (the issue-0033 field). It never earned its keep on any model, so we deleted it rather than ship dead off-by-default config. Every thematic group always runs.

## Cost note (relevant to your one-model-per-customer deployment)

Ingest cost rises: every extractable prose span is now extracted (~7 thematic tag-parse calls each via the ADR-0081 extractor), where before only tagged spans were. The knobs:
- **`is_extractable_span`** is the span-count governor. If you see furniture-ish prose you'd rather not pay for, tell us the form and we tighten it (deterministically, conservatively).
- **Span batching** (two spans per group call) is the next lever — in progress now. It halves the calls while preserving per-group focus (so no property loss, unlike the gate). We'll A/B batched-vs-per-span before adopting it and hand you the numbers.
- Group pruning is off the table (the gate proved it costs recall).

## If you want to hold clause counts steady while you validate

The behavior is on by default. There's no flag to revert only the guard (it was a bug, not a mode). If you need to compare old-vs-new on a corpus, re-ingest a sample and diff clause counts / property coverage; we can help design that.

Reference: commit `6b69ab4`, ADR-0101, `subgraphs/contract_ingestion_pipeline.py::clause_extraction_jobs`, `spans/segment.py::is_extractable_span`, `spans/tag_clause_extractor.py` (aspect gate removed). Full suite: 1549 passed.
