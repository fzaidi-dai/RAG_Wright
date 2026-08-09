# ADR-0047: Retire the precomputed clause-function pre-filter (Option B); it is not load-bearing for recall

## Context

PREC-1b surfaced that ingestion mislabels a clause's `function` (e.g. a force-majeure clause labeled
`Cap On Liability` by the LegalBERT classifier, macro-F1 0.5885). Before investing in fixing the labeler
(Option A), we asked whether the query pipelines even need the precomputed clause `function`.

Three pieces of evidence, all pointing the same way:
1. **Static audit.** Of the 4 Tier-1 legs, only `typed_property_retrieval` (Leg B) hard-depends on the
   precomputed function — as a candidate-pool pre-filter, `span_hybrid_search(function=f)`
   (`property_boosted_retrieval.py`). `relational_qa` is graph-structural, `intra_document_qa` is contract-scoped
   (a convenience narrowing over ~10-80 clauses), `compliance_check` matches requirements by property scope.
2. **Top-8 cited-span probe** (7 typed queries, local KG + Cerebras/Gemma-4): function-gate OFF (whole-index BGE +
   property rerank) reproduced ON at **7-8/8** — the gate only trimmed one tail semantic-neighbor.
3. **Graded recall** on the unified KG (ADR-0046) over 57 ACORD attorney-graded queries
   (`docs/eval/function_gate_recall.md`): ON (oracle-function) ≈ OFF (whole-index) within ±0.02 at every K, in
   both raw and reranked modes, with OFF marginally *ahead*; the single-function CEILING is **0.969** (the gate
   makes 3.1% of relevant clauses unreachable, a ceiling whole-index does not have).

## Decision

**Retire the precomputed clause `function` as a retrieval pre-filter (Option B).** Leg B's candidate pool becomes
the **whole-index** BGE hybrid pool; the property-constraint boost + rerank stay. The function signal is no longer
a hard gate on the pool. Where a leg genuinely benefits from function typing, derive it **at query time** on the
retrieved candidates (a strong LLM over ~25 candidates), not as a static ingestion-time label baked into the pool.

Rationale: the gate does not buy recall, it caps recall at 0.969, and — decisively for the PREC-1b concern — the
gate is the very mechanism by which a clause **mislabel** corrupts retrieval (a mislabeled clause pollutes the
filtered pool). Removing the gate makes retrieval by-meaning and shrinks a mislabel's blast radius, so we get the
robustness fix without a costly corpus relabel.

## Consequences

- **Leg B change (implementation, next task):** default `property_boosted_retrieval` / `typed_property_retrieval`
  to a whole-index pool (functions → empty), keeping the property boost + rerank. The query function classifier
  is no longer on Leg B's critical path.
- **The clause `function` LABEL stays in the KG** for now (cheap; used by intra-doc's contract-scoped serve and as
  a returned tag), but is no longer a corpus-wide retrieval gate. Whether to stop computing it at ingestion (drop
  the LegalBERT pass) is a separate, later call.
- **PREC-1b (fix the labeler) is de-prioritized:** the mislabel's main harm was via the gate, which is now gone.
  The generation-side honesty fixes (PREC-1a: `[auto-tag:]` framing + hedge) remain the residual mitigation.
- Evidence + harness are committed: `docs/eval/function_gate_recall.md`,
  `scripts/legb_function_gate_recall.py {raw|rerank}`.
