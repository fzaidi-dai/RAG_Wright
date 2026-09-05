# ADR-0082: The symbolic clause-validation gate becomes function-INDEPENDENT (revises ADR-0040)

Date: 2026-09-05
Status: Accepted (implemented)

Revises ADR-0040 (JUDGE-ONTOLOGY-1/2/3, the symbolic validation gate). Consequence of the measured finding in
ADR-0079 + memory `function-classification-not-load-bearing`, and the function-independent extraction of ADR-0081.

## Context

`symbolic_validate` (ADR-0040) downgraded to `AMBIGUOUS` any assertion whose dimension violated a SHACL shape:
1. `sh:closed` — the dimension is **not applicable to the clause's function**;
2. `sh:in` — a **deontic-polarity** value on a restrictive function;
3. `sh:maxCount 1` — a **scalar dimension asserted with conflicting values** (an intra-clause contradiction).

Checks (1) and (2) are **function-load-bearing**: they downgrade based on the clause's function. But clause-function
classification is only ~0.5 top-1 (not load-bearing), and clause extraction is now function-INDEPENDENT (ADR-0081).
The grounded A/B (raw-vs-grounded diagnostic) showed the concrete harm: on a License Grant clause the gate
downgraded a correct `mutuality=unilateral` (a license grant *is* unilateral) because License Grant's applicability
map is narrow — even with the *correct* gold function. On a mis-classified function it is worse. So a coin-flip,
narrow-map signal was demoting **correct cross-cutting extractions**.

Check (3) is **function-INDEPENDENT** and genuinely valuable (a clause can't assert two conflicting values for one
scalar dimension).

## Decision

`flagged_dimensions` keeps **only the function-independent contradiction check** (`sh:maxCount`), filtering SHACL
results by `sh:sourceConstraintComponent == sh:MaxCountConstraintComponent`. The function-dependent `sh:closed`
(applicability) and `sh:in` (deontic polarity) violations are **deliberately ignored**. Function is a KG tag /
query-time soft signal (retrieval boost, ADR-0047 style), never an ingest-time downgrade gate.

The applicability map stays in the ttl (source of truth, ADR-0066) — unused by this gate now, still available for
query-side soft signals; the shapes are unchanged (we filter results, not the shapes).

## Consequences

- **Correctness win**: cross-cutting properties (mutuality / favorability / party_asymmetry) that a narrow function
  map wrongly excluded are no longer downgraded — they stay `EXTRACTED` in the KG (not down-weighted). The
  discriminative-dim recall is unchanged (~0.40 granite / 0.56 gemma on the 45-clause A/B) because those dims were
  function-applicable anyway — the fix is about the cross-cutting dims, which that metric excludes.
- **Consistent architecture**: function is now non-load-bearing everywhere in ingestion (extraction AND validation);
  it is a soft tag. Matches ADR-0047 (function gate not load-bearing for retrieval).
- **Tradeoff**: a function-inappropriate but lexically-grounded value can now survive ingest (a stray "12 months" →
  cap_quantum on a non-cap clause). That is handled by treating function as a **soft query-time signal**, not a
  hard ingest gate — the right place for an unreliable signal. Lexical grounding (ADR-0028) + the value-sanity
  guard (ADR-0081) + the retained contradiction check still filter noise.
- **No downstream breakage** (full suite green); the 7 function-dependent symbolic tests were rewritten to assert
  the new function-independent behavior; the contradiction tests are unchanged and still pass.
