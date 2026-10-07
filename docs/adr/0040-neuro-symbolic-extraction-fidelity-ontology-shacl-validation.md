# ADR-0040: Neuro-symbolic extraction fidelity — ontology-driven SHACL validation + a narrowed semantic-judge

> **Status: REVISED (ADR-0082).** The symbolic clause-validation gate became function-INDEPENDENT (ADR-0082); the rest of the neuro-symbolic cascade stands.


Date: 2026-08-03
Status: Accepted (design; implementation staged as JUDGE-ONTOLOGY-1..3 + JUDGE-SEMANTIC)

## Context

The property-grounding judge (ADR-0028, `spans/property_grounding.py`) is **lexical only**: it checks that an
`EXTRACTED` value's surface cue appears in the clause, on the *lexically-anchored* dimensions. It cannot check
the *semantic* dimensions (mutuality, favorability, party_asymmetry, cap interpretation), and — as the vLLM
validation surfaced (MODAL-STACK-2) — it lets three classes of error through as "grounded":

1. **Type errors** — a dimension asserted that does not belong to the clause's function (e.g.
   `nonsolicit_target=employees` on an Anti-Assignment clause).
2. **Deontic-inversion errors** — a value whose polarity contradicts the clause's rule type (e.g.
   `assignment_consent=free` on a "shall not sub-license" *prohibition*).
3. **Ungrounded open-valued inference** — a value on an open-valued dim with no textual basis (e.g.
   `jurisdiction=United States`), which the lexical judge skips because the dim is not in its cue map.

This is a **granite-level limitation** (same on OpenRouter and vLLM — the model + judge are identical), so it is
a *product-quality* problem, not a substrate one. The key realization: errors (1)–(3) are **type / consistency /
grounding** violations, not text-*reading* violations — and those are exactly what an ontology validates. Our
`contract_bridge.ttl` already encodes the scaffolding (`owl:oneOf` ×25 value vocabularies, `rdfs:domain/range`
×40, `owl:FunctionalProperty` ×33 cardinality, `odrl:` ×10 deontic) but we **validate none of it** except the
value vocab in code (`CLOSED_VOCAB`); there are **0 SHACL shapes and 0 OWL restrictions**. We have the T-Box; we
lack the reasoner.

## Decision

Add a **symbolic validation layer** (ontology-driven, deterministic, no LLM) between extraction and the KG write,
then a **narrowed LLM-semantic-judge** for the irreducible residual. The extraction-fidelity pipeline becomes a
cascade, each layer clearing what it is best at and shrinking the next:

- **Layer 1 — lexical grounding** (exists): surface-cue check on anchored dims → ungrounded ⇒ AMBIGUOUS.
- **Layer 2 — symbolic ontology validation (NEW, deterministic)**: represent the extracted clause as RDF (clause
  instance + its FOLIO function + its property assertions) and validate against **SHACL shapes derived from
  `contract_bridge.ttl`** (via `pyshacl`). Shapes:
  - **dimension applicability** — each FOLIO clause function permits a set of dimensions; a non-applicable
    dimension is a violation (kills error class 1). *Requires an ontology addition: a formalized
    `function → applicable-dimensions` mapping (today `rdfs:domain` points at constraint classes, not functions).*
  - **value ∈ vocabulary** — `sh:in` from `owl:oneOf` (formalizes the existing `CLOSED_VOCAB` check as a shape).
  - **cardinality** — `sh:maxCount 1` from `owl:FunctionalProperty` (scalar dims).
  - **deontic consistency** — the clause's ODRL rule type (permission / prohibition / duty) vs the value's
    deontic polarity; a permission-polarity value on a prohibition clause is a violation (kills error class 2).
    *Requires additions: deontic-polarity tags on values + a deontic-type signal for the clause.*
  - **cross-dimension rules** — `sh:sparql` constraints (e.g. `cap_basis=uncapped ⇒ no cap_quantum`).
  A SHACL violation flags/downgrades the offending assertion (to AMBIGUOUS, with the violation as provenance) —
  a permanent gate like `reground`, and it applies to EVERY dim (including semantic ones — structure is
  checkable even when meaning is not).
- **Layer 3 — narrowed LLM-semantic-judge (NEW)**: only for the dims neither lexical nor symbolic can reach
  (mutuality, favorability, party_asymmetry, cap interpretation). A cheap, targeted verify-or-refute call that
  re-cites the span. Because layers 1–2 already cleared the type/consistency/deontic errors, this call class is
  small, focused, and the only place an LLM is spent on judging.

**Why SHACL/ontology and not Python rules:** the domain rules must live in the ontology (the domain source of
truth, ADR-0037), declaratively, not hardcoded — so they are auditable, extensible, and shared with the
`.ttl`/spec. `pyshacl` validates the extracted A-Box against shapes authored alongside the ontology.

## Consequences

- **Catches most of the observed residual deterministically** (type + cardinality + deontic + cross-dim), with
  no LLM and no network — the symbolic half of neuro-symbolic. Directly lifts Leg-A/B extraction fidelity.
- **Honest boundary:** the ontology validates the *output space + internal consistency*, NOT the text→value
  *reading*. A value that is in-vocab, applies to the function, is consistent, but is simply the wrong reading
  of *this* clause (e.g. `mutuality=mutual` on a one-sided clause) is invisible to SHACL/OWL — that is the
  irreducible semantic core, and it is exactly what Layer 3's LLM-judge exists for. Symbolic for what must be
  *logically* true in the domain; neuro for what must be *read* from the text.
- **Ontology work is additive** to `contract_bridge.ttl` (function→dimension applicability, value
  deontic-polarity, cross-dim rules, a shapes graph) — the scaffolding is already there.
- New dep: `pyshacl` (and `rdflib`, likely already transitive) — pure-Python, no network at validation time.
- The open-valued-inference gap (jurisdiction, error class 3) is a **grounding-coverage** fix (extend the
  lexical judge to open-valued dims), tracked separately — it is not an ontology-semantics win, and saying so
  keeps the boundary honest.

Staged: **JUDGE-ONTOLOGY-1** (function→dimension applicability — the biggest deterministic win, do first),
**JUDGE-ONTOLOGY-2** (cardinality + cross-dimension SHACL), **JUDGE-ONTOLOGY-3** (deontic consistency),
**JUDGE-SEMANTIC** (narrowed LLM-semantic-judge), and **GROUNDING-OPENVALUED** (open-valued grounding coverage).
See ADR-0028 (lexical judge), ADR-0033 (unified KG), ADR-0037 (ontology is the source of truth), memory
[[property-grounding-judge]].

## ING-9 / ING-9b addendum (2026-10-07): the judge and the residual dimensions on the decision model

- **Layer-3 judge on Jev (ING-9).** `spans.semantic_judge.DecisionJudge` asks the decision model (`jev_decision`,
  the default `jev-1.13` profile) to verify ALL of a provision's semantic values in ONE batched call (the SKILL's
  strictness rule stated once, one `noul` per value; < 0.5 refutes -> AMBIGUOUS; any error leaves values untouched).
  The default when a decision model is configured; an explicit `judge_model` or `RAG_SEMANTIC_JUDGE=llm` selects the
  LLM judge. Measured on 192 judge cases hand-labelled blind to both judges (the classifier test sets' silver labels
  agreed with careful labels only 64% of the time, so they were not used): Jev 95.3% vs the LLM judge 90.1%
  (catches 149/154 unsupported values vs 138/154), calibrated scores, one call per provision instead of one per value.
- **Residual dimensions on Jev (ING-9b).** The 7 numeric/open dimensions (cap amount, jurisdiction, time bound,
  notice period, audit frequency, committed quantity, liquidated-damages trigger) were one LLM call per provision.
  Now `spans.residual_candidates` proposes candidate spans deterministically (amounts, durations, dates,
  frequencies, quantities, jurisdiction phrases, cap formulas, term references) and the decision model says what
  each IS in its provision -- one `choice` per candidate among the roles authored in contract_bridge.ttl
  (`cbr:ResidualRole` + `cbr:decisionCriterion`, ADR-0066), ONE call per provision and NONE without candidates.
  Values are de-duplicated (a value restated in parentheses kept once, digits preferred; an overlapping phrase gives
  way to the value it contains) and jurisdictions reduced to the place name. The LLM call remains only as the
  fallback (no decision model, or `RAG_RESIDUAL_EXTRACTOR=llm`). Measured on set-A provisions hand-labelled before
  scoring (held-out, value level, shipped code, live calls): recall 0.83 / precision 0.88 vs the LLM's 0.60 / 0.79.
  Candidate coverage caps recall (the candidates cover 95% of the values the LLM produced). Known leftovers: a
  jurisdiction phrase can run into a following name ('Singapore and Ability Computer'), and a regulation mention can
  be labelled a jurisdiction ('California Escrow').
- **The extraction method is part of the clause-cache key** (`extraction_method`), so switching the judge or the
  residual lane never reuses records the other method produced.
- **Live (Aimmune, 131 provisions):** 215 calls, all Jev, $0.013, 276 s, zero LLM calls -- vs 277 calls (276 LLM),
  $0.277, 459 s with the LLM judge + LLM residual. The judge kept/downgraded 92.6% of 363 semantic values the same way
  as the LLM judge; the residual lane stored 95 values vs 41 (consistent with its higher measured recall).
- Harnesses: `eval/semantic_judge_gold.py` (`--score-blind`), `eval/residual_decision_gold.py`; gold data local only.
