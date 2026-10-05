# T58 property-graph population — run report (2026-07-24)

The state of the pivot store when we ran the full function+property+rerank recall@50 measurement. This is the
checkpoint the GATE-R number is measured against.

## What was run

- **Phase 1** (`PHASE=spans`, ADR-0025): segment every ACORD clause -> classify each span's function
  (T60 LegalBERT, 45-class) -> BGE-M3 dense+sparse embed -> `upsert_span`. Result: **14,553 spans over 3,931
  clauses**, 3,887 non-NONE clauses cached for phase 2. DB: `ragwright_acord_pivot`.
- **Phase 2** (`PHASE=extract`, `EXTRACT_MODEL=deepseek-v4-flash ESCALATE_MODEL=deepseek-v4-pro GATE=1`):
  the Flash->Pro grounding-judge cascade (ADR-0028) with the quality gate on, both models throughput-routed
  (ADR-0027). Ran in **~41 min, fully flowing** (vs the ~6h stall on the earlier cheapest-provider routing).
  Crash-safe per-clause writes, resilient (0 skips / 0 errors), resumable.

## Populated property graph

- **3,886 clauses, 393 shared value nodes, 8,597 property edges** (one straggler clause hung on a Pro retry:
  3886/3887, resumable).
- Confidence distribution on `HasProperty` edges: **EXTRACTED 6,196 (72%) / INFERRED 1,764 (20%) /
  AMBIGUOUS 637 (7.4%)**. The 7.4% AMBIGUOUS = genuine novel values + `reground` (GATE) downgrades of
  ungrounded `EXTRACTED` -> down-weightable in soft-boost, not asserted as fact.
- Closed-vocab dimensions deduped tightly (16 distinct `carve_out`, 3 `mutuality`, 7 `favorability`); open
  dimensions spread as expected (123 `cap_quantum`, 51 `jurisdiction`, 68 `temporal_bound`).
- Top carve-out shared nodes (clauses -> node): indemnification 98, gross_negligence 71, willful_misconduct
  70, confidentiality 59, third_party_ip_infringement 36, fraud 35, bodily_injury 35, applicable_law 27. A
  property query traverses `(:PropertyValue{carve_out:X})<-(:Clause)` straight to its candidate clauses.

## Baselines / bars in play (for the recall@50 measurement)

- Two-leg baseline (pre-pivot): recall@50 **0.379** (ADR-0022).
- Function-gate reachability ceiling (T58a): best-single **0.939** / union-top-2 **0.992**.
- Function + BGE-rerank, NO property (T58b ablation): recall@50 **~0.52** (property leg shown load-bearing).
- GATE-R bar: recall@50 **>= 0.667**.

## Reproduce / resume

- Store: ArcadeDB DB `ragwright_acord_pivot` (spans + property graph). Not in git (data artifact).
- Re-populate: `PHASE=spans ...` then `EXTRACT_MODEL=deepseek/deepseek-v4-flash
  ESCALATE_MODEL=deepseek/deepseek-v4-pro GATE=1 PHASE=extract uv run python -m scripts.populate_property_store`
  (RESUME skips done clauses; `FRESH=1` restarts extraction keeping spans).
- Related: ADR-0025 (pivot), ADR-0026 (property schema), ADR-0027 (routing), ADR-0028 (judge+cascade);
  memory `retrieval-design-t58`, `property-grounding-judge`.
