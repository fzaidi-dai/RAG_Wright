# ADR-0122: Provision-boundary detection is deterministic-first with a decision-model fallback for the residue

- Status: Accepted
- Date: 2026-10-06
- Context: engine-prep PREP-2.5 diversion — `intra_document_qa` abstained on a valid ingested document.

## Context

`intra_document_qa` returned "the retrieved context does not support an answer" for a document that plainly
contained the answer. Root-causing (verify-before-acting) found **three** real defects, not one:

- **A — provision under-splitting.** `spans.segment.starts_new_provision` recognized a bare leading number (`8.`,
  `2.1.`) but not the dominant `Section N` / `Article N` / `Clause N` / `§N` heading style, so a contract using
  section-WORD headings collapsed into a single provision → one clause. (Only bare-number contracts split, which is
  why the CUAD eval had not caught it.)
- **B — lost citation anchor.** `spans.property_extractor.HybridPropertyExtractor._record` built the
  `ClausePropertyRecord` without the record-level `span_id`, so a property-less clause had no operative-span anchor
  and could not be rehydrated for citation.
- **C — serve regression (decisive).** `ContractKGStore` (the EP-REF domain store wrapper) delegated
  `clauses_in_contract` but was **missing `all_spans_by_contract`**, which `contract_clause_index(..., include_untyped=True)`
  always calls. `serve` raised `AttributeError`, its try/except degraded to empty clauses, and `intra_document_qa`
  abstained for **every** document. The e2e test only asserted a non-None result (an abstention is non-None), so the
  regression shipped silently.

## Decision

1. **Provision-boundary detection is deterministic-first, with a decision-model fallback for the UNCERTAIN residue
   only** (the neuro-symbolic shape, ADR-0040). `spans.segment`:
   - `starts_new_provision` is extended to recognize section-WORD prefixes (`Section`/`Article`/`Clause`/`Sec.`/
     `Art.`/`§` + a depth-capped number, trailing period optional) in addition to bare numbers and headings. This is
     the cheap, exact, domain-agnostic path for the clear majority.
   - `provision_boundary_verdict` returns `start` / `continue` / `uncertain`. Only the `uncertain` residue — short,
     plausibly-heading lines in styles the rules do not confidently classify — is sent to a model.
   - `spans.boundary.adecide_provision_starts(texts, decider=…)` resolves the residue via a pluggable async decider;
     `jev_boundary_decider` invokes the `jev_decision` capability THROUGH the engine invoker in **one batched call**
     (all candidate lines in the `state`, one `noul` question each). The decider is **optional and
     graceful-degrading**: with no decision model configured, or on any error, an uncertain span folds in (the prior
     deterministic behavior) — so ingestion never requires a decision model and the hermetic tests stay offline.

   Why not regex-only: new document conventions keep arriving; a rule set is rigid. Why not a model per span: that
   is the per-unit cost the decomposition analysis warns against. Deterministic-first + model-for-the-residue is
   flexible where regex is rigid and bounded in cost (the model fires only on short, ambiguous lines).

2. **A clause always carries its operative-span anchor** (`span_id`), even when property-less (`_record` now threads
   it) — the ADR-0025 1:1 citation join the serve step rehydrates from.

3. **`ContractKGStore` delegates `all_spans_by_contract`** to the generic store (as it already did
   `clauses_in_contract`), so the Leg-A serve path works.

## Consequences

- `intra_document_qa` returns grounded, cited answers again (live: a 5-provision contract → a cited liability-cap
  answer, citation to the operative span). Regression-tested: `tests/spans/test_segment.py` (section-word starts +
  the 3-way verdict), `tests/spans/test_boundary.py` (deterministic-only / residue-routing / error-degrade),
  `tests/spans/test_property_extractor.py` (property-less clause keeps its span_id),
  `tests/capabilities/test_contract_kg_store_reads.py` (the `all_spans_by_contract` delegation).
- New heading styles are handled by the decision model, not by chasing regex corner cases; cost stays bounded to the
  short ambiguous residue, and the whole path degrades to deterministic when no decision model is present.
- Follow-up worth considering (not required): a live `intra_document_qa` assertion that checks a **non-abstained,
  cited** answer, so a serve-path break like (C) fails CI instead of passing as "non-None".
