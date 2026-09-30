# ADR-0115: Classifier-only Step-3a property extraction (21-dim best-of-both fleet + one residual numeric LLM call)

## Status
Accepted (2026-09-30). **Refined by ADR-0116**: the classifier lane's "function-independent" framing below was
softened to SOFT function-scoping after CLS-D found that classifiers (which cannot abstain) over-emit on the
subjective dims — see ADR-0116. Supersedes the CLS-B function-aware `HybridPropertyExtractor` sketch; builds on
ADR-0049 (generic-customer lens), ADR-0081/0049 (function-independent extraction), ADR-0030 (adopt-only-if-better),
ADR-0028 (grounding gate), ADR-0040 (symbolic gate), ADR-0114 (SetFit clause-function classifier).

## Context
Step-3a ("Extract Clauses") extracted the ~36 `PropertyDimension` values via the tag-parse `Clause` extractor:
~7 thematic-group LLM calls per provision (ADR-0081). That per-provision LLM cost multiplies for large-scale
ingestion and is the ingestion-latency lever. The standing goal is to replace the LLM decision points with trained
classifiers.

We validated a fleet by measurement, not assumption:
- A grouped-Laya bake-off (ModernBERT-large, RL decision head) swept 3 grouping schemes × 21 dims, then a top-k
  re-eval, then a **SetFit adopt-only-if-better overlay** on the same gold test sets (test-set parity confirmed).
- Result: **21 dims clear the per-value floor (>0.65) at deploy-k** — Laya primary (14 wins), SetFit best for
  `carve_out`/`covered_parties`/`nonsolicit_target`, 3 ties. `cap_basis` (numeric) and `renewal_mechanism`
  (subjective) do not clear on any method.
- The remaining 15 `PropertyDimension`s split into **7 numeric/open** (jurisdiction, notice_period, cap_quantum,
  temporal_bound, audit_frequency, commitment_quantum, ld_trigger) that a classifier **cannot** emit (value
  extraction, not label selection), and **8 corpus-starved closed-vocab** dims (dispute_method, royalty_basis,
  condition_type, collateral_type, force_majeure_event, confidentiality_exception, right_of_first_type,
  escrow_release_trigger) that CUAD/the KG barely contain (0–1 values; 3 have no KG edge) — a data gap, not a
  method gap, sourceable from MCC/SEC-EDGAR/MAUD/LEDGAR (ADR-0049: judge by "does a new customer benefit?",
  not CUAD coverage).

## Decision
Step-3a property extraction is **classifier-first, function-independent**, and is NOT a toggle over an LLM
fallback. `HybridPropertyExtractor` (implements the `PropertyExtractor` Protocol) has two lanes:
1. **Classifier lane** — every dim the `DimClassifierRegistry` covers is answered by its trained
   `DimClassifier` regardless of the clause function (function is a soft tag on the record, never a gate).
   Top-k soft tags: rank-0 EXTRACTED, lower ranks INFERRED. `ACCEPT_WEAK_DIMS` (`cap_basis`,
   `renewal_mechanism`) are emitted AMBIGUOUS (served locally, low-confidence; never routed to the LLM).
2. **Residual LLM lane** — ONE consolidated call for `RESIDUAL_LLM_DIMS` (the 7 numeric/open dims) ONLY. The
   LLM is never asked for a classifier dim; any other dim it volunteers is filtered out. This lane is
   **permanent** — the 7 are extractive and unclassifiable.

The **8 corpus-starved dims are not extracted** until CLS-F sources data; on success they join the **classifier
lane**, never the LLM. This is near-costless now (the KG holds ~0 of them).

There is **no config toggle, no default-to-old, and no path back to the full-LLM tag-parse for the 21/8 dims**.
The classifier lane is the decided path; CLS-D validates it live and any shortfall is fixed forward.

The downstream ADR-0028 grounding + ADR-0040 symbolic gates and the `ClausePropertyRecord` contract are
unchanged. The fleet is served behind the device-agnostic seam (GPU-if-available-else-CPU), same philosophy as
the model-profile seam.

## Consequences
- Per-provision LLM calls drop from ~7 to **1** (the numeric-only call) — the latency lever, realized.
- The classifier lane is a **best-of-both** fleet (Laya + SetFit), served behind one registry; not a single
  framework. Adopt-only-if-better per dim (ADR-0030).
- `cap_basis`/`renewal_mechanism` carry AMBIGUOUS confidence until a better model/more data clears the bar.
- The 8 dims are a known, deliberate extraction gap until CLS-F; raw sourced data stays uncommitted (CUAD
  discipline).
- Routing constants (`RESIDUAL_LLM_DIMS`, `ACCEPT_WEAK_DIMS`) live in code for now, like `FUNCTION_DIMENSIONS`
  and `CLAUSE_GROUPS` — a candidate for ontology migration (ADR-0066).
- CLS-D must fetch the 21-dim classifier fleet locally and make the residual prompt numeric-aware before the
  live run.
