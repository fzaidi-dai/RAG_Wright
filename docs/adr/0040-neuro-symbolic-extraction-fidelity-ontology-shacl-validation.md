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
