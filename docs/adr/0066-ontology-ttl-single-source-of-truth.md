# ADR-0066 (DRAFT / PROPOSED): The ontology `.ttl` is the single runtime source of truth for domain knowledge

Date: 2026-08-31
Status: **Accepted** (design approved 2026-08-31; implementation phased, each phase gated)

Extends and corrects the *knowledge-location* posture of ADR-0037 (hand-maintained clause template), ADR-0040
(the validation cascade with its rules in Python "for no ttl drift"), and ADR-0065 (the DEON query-side gates,
whose routing rules are also Python). Supersedes the "Python is authoritative to avoid `.ttl` drift" stance of
ADR-0037/0040 with a synchronization design that keeps the ontology authoritative.

## Context (the honest current state, from the 2026-08-31 ingestion review)

The engine is neuro-symbolic in *mechanism* (typed KG fields gate the LLM; the ADR-0040 cascade runs), but the
symbolic *knowledge* is authored in Python and the ontology is decorative at runtime. Worse, there is no single
source of truth — there are **three partial, drifting sources**:

- `ontology/contract_bridge.spec.yaml` — a YAML of enums + synonyms (a machine-readable source).
- `ontology/contract_bridge.ttl` — the RDF ontology, but **INCOMPLETE** (8 `owl:oneOf` vs ~25 dimensions in
  `CLOSED_VOCAB`) and with **ZERO SHACL** shapes. The `function → applicable-dimensions`, cardinality, and
  deontic-polarity constraints ADR-0040 said to add "to the ontology" were added in **Python**, not the ttl.
- Python — `contracts/property.py::CLOSED_VOCAB`, `spans/symbolic_validation.py::FUNCTION_APPLICABLE_DIMS`,
  `store/arcadedb.py::_TYPED_DIMENSION_EDGE`, `subgraphs/compliance_check.py::{SECTION_RULE_SCOPE, _ACTOR_SYNONYMS}`
  — the **de facto authority** at runtime.

No `.ttl` is parsed by running code anywhere (query side included). The one "drift guard"
(`tests/ontology/test_clause_template.py`) compares Python-to-Python; nothing binds the ttl to the code.

The root mis-resolution (the thing this ADR fixes): `ontology/clause_template.py` correctly observes that the ttl
"cannot express" the extraction prompt-engineering (LLM-guiding field descriptions, `examples`, brevity limits,
`_normalize_enum` validators) — a real constraint. But it resolved that by **merging** vocab (knowledge) and
prompt-tuning (mechanism) into one hand-maintained `.py` "allowed to deviate from the ontology," and demoting the
ttl to a one-time bootstrap. So a legitimate "the source can't express X" problem was solved by moving the source
of truth into code, and the vocabulary now drifts freely.

## Decision — two standing architecture rules

### Rule 1 — The ontology `.ttl` is the single runtime source of truth for domain KNOWLEDGE; code holds MECHANISM only.

Domain knowledge lives in the ontology, declaratively, engine-wide (ingestion AND query). Code holds only behavior.
The line, drawn explicitly so this does not become "stuff everything into RDF":

| Concern | Lives in | Examples (today, to migrate) |
|---|---|---|
| **Vocabulary** (closed value sets) | ontology `owl:oneOf` | `CLOSED_VOCAB`, `ClaimType`, `DeonticType`, `Severity` |
| **Schema** (classes, properties, KG edge types) | ontology (FOLIO/ODRL classes + edge decls) | `_TYPED_DIMENSION_EDGE`, the ArcadeDB DDL classes |
| **Constraints** (applicability, cardinality, deontic polarity) | ontology **SHACL** (`sh:NodeShape`) | `FUNCTION_APPLICABLE_DIMS`, `MULTI_VALUED_DIMENSIONS`, `PERMISSION_POLARITY_VALUES`, `RESTRICTIVE_FUNCTIONS` |
| **Mappings / synonyms / rollups** | ontology `skos:altLabel` / `skos:broader` | `_ACTOR_SYNONYMS`, `VALUE_ROLLUP`, FTC `SECTION_*` overrides (as a domain-pack ttl) |
| **Mechanism** (the pipeline, the router, the judge, the cascade) | **code** — stays | `deontic_route` logic, `symbolic_validate` runner, `reground`, the judges |
| **Field descriptions / examples** (LLM-guiding knowledge) | ontology `skos:definition` / `skos:example`, GENERATED into the template's field metadata | `Field(description=...)`, `examples=...` in `clause_template.py` |
| **Prompt mechanics + normalizers** (brevity/format instructions, the normalize algorithm) | the extraction PROMPT (a Skill) + clean **code** — NOT the ontology, NOT byte-generated (Rule 3) | the terse "answer ONLY..." instructions, `max_length`, `_normalize_enum`, `__str__`, the dedup model-validators |

### Rule 2 — Solve staleness with enforced synchronization, never by demoting the source.

When an authoritative source (the ontology) must feed a consumer that also carries concerns the source cannot
express (prompt-tuning), **SEPARATE** the concerns into `(authoritative source) + (declarative overlay)` and
**COMPOSE** them by generation, with **CI enforcing zero drift** (regenerate → diff → fail on any delta).
NEVER merge the two into one hand-edited artifact and demote the source. A generated-file-goes-stale problem is a
*tooling* problem (make regeneration deterministic and CI-enforced); trading it for "truth lives in code" is an
*architectural* problem — strictly worse, and the origin of hodge-podge. This rule is general and binds future work.

### Rule 3 — When generating code from the ontology, generate the KNOWLEDGE; keep MECHANISM as clean code.

A generator that produces code from the ontology must emit only the **knowledge-bearing** content (vocabularies,
field descriptions, examples, the schema the ontology declares). The **mechanism** in that same artifact
(normalizers, `__str__`, model-validators, dedup logic, config, helpers) STAYS as ordinary hand-authored code —
it is drift-locked to the ontology by a test, not byte-generated. Do NOT byte-generate mechanism: that would drag
clean, readable code *into* a generator as string-templates, which is the very inversion Rule 1 forbids (code
encoded as data). The test for what to generate is Rule 1's line — "is this knowledge or mechanism?" — applied to
every span of the artifact, not to the artifact as a whole. Consequence: a "generated" file is usually a small
generated **knowledge module** that clean hand-code **consumes**, plus drift tests locking the hand-code's
structure to the ontology — NOT a wholesale machine-emitted file. (This is why ADR-0066 P1b-2 chose Option B:
generate the field descriptions/examples + the vocab enums from the ttl; keep the validators / `__str__` / dedup
model-validators / configs as clean code, drift-locked. Byte-generating them — Option A — was rejected precisely
because it violates this rule.)

## How the drift problem is actually solved (the mechanism behind Rule 2)

- **Generated code (enums / `CLOSED_VOCAB` / edge-map)** is produced FROM the ttl by a deterministic generator,
  committed, and a CI check regenerates + diffs. Any hand-edit to a generated file, or any ttl change not
  regenerated, fails CI. Drift is impossible by construction. (Build-time generation, not runtime, because these
  are Pydantic types imported as static types across the codebase.)
- **SHACL constraints** are authored IN the ttl (`sh:NodeShape`) and loaded at RUNTIME by `pyshacl` — the symbolic
  layer finally reads the symbolic artifact. The Python `FUNCTION_APPLICABLE_DIMS` / `_shapes_graph` are deleted.
- **The prompt-engineering overlay** (the thing the ttl genuinely cannot express) is authored in a SEPARATE
  declarative file keyed by field, and composed onto the generated schema at build time. Regeneration never
  clobbers it because it is not in the generated file — which removes the entire original reason ADR-0037 gave for
  making the template hand-authoritative.

Net: one source (the ttl), everything else generated-and-enforced or runtime-loaded, tuning preserved as a
first-class separate concern.

## Phased plan (each phase: TDD + a gate; each ends at a review)

- **Phase 0 — consolidate to ONE source.** Make `contract_bridge.ttl` COMPLETE and authoritative: port the full
  vocab (all ~25 `owl:oneOf`), synonyms (`skos:altLabel`), value rollups (`skos:broader`), and author the
  constraints as `sh:NodeShape` (function→dimension applicability, scalar cardinality, deontic polarity). Retire
  `contract_bridge.spec.yaml` (or regenerate it FROM the ttl). Gate: the ttl alone reproduces today's Python
  `CLOSED_VOCAB` + `FUNCTION_APPLICABLE_DIMS` exactly (a one-time equivalence test).
- **Phase 1 — generate vocab/schema from the ttl + CI-diff; move the extraction knowledge INTO the ttl.**
  - **P1a (DONE 2026-08-31):** generate `CLOSED_VOCAB` from the ttl into `_generated_vocab.py`; `property.py`
    consumes it (hand-authored literal deleted); CI drift-diff (`test_generated_vocab_in_sync.py`) fails on any
    ttl-edit-not-regenerated or hand-edit; live A/B (real extraction identical). Proves the generate + enforce
    pattern.
  - **P1b-1 (DONE 2026-09-01):** audit `clause_template.py` field-by-field, then CAPTURE its full schema +
    knowledge into the ttl keyed by field (`cbr:field_<Model>__<name>`: kind, default, enum/model ref, edge,
    max_length, `skos:definition`, ordered `cbr:examples`). Shared introspection (`template_introspect.py`) feeds
    both the emitter and the drift test (`test_template_captured_in_ttl.py`, negative-proven). Audit findings: the
    template has NO hand-authored synonyms (the normalizer is a generic algorithm → stays code, no `skos:altLabel`
    needed); descriptions carry the LOOK-FOR meaning + examples + a terse instruction; and **10 of 43 fields have
    EMPTY descriptions** (`# TODO` gaps). Captured verbatim (empties as empty) so P1b-2 reproduces the template
    exactly. No live A/B here (P1b-1 changes no model path).
  - **P1b-2 (next) — Option B (Rule 3): generate the KNOWLEDGE, keep MECHANISM as clean drift-locked code.** The
    920-line template is mostly mechanism (2 helpers, ~25 trivial per-field normalizer validators, 3 bespoke
    `_deduplicate_*` model-validators, 4 bespoke `__str__`, configs, the `CAP_OTHER`/`OTHER` enum quirks). So the
    generator emits only a **generated knowledge module** (each field's `description` + `examples` from the ttl
    capture; the vocab enums are already ttl-generated via P1a + drift-locked); `clause_template.py` CONSUMES that
    module for its field metadata and keeps all mechanism as clean hand-code. Full-file byte-generation (Option A)
    was REJECTED — it would drag the bespoke mechanism into the generator as string-templates, violating Rule 3.
    Gate: `uv run <gen>` idempotent; CI drift-diff fails on a hand-edit; a **live A/B (parity)** proves real clauses
    extract IDENTICALLY to today's template.
  - **P1b-3 (MEASURED IMPROVEMENT — only AFTER P1b-2 parity is proven):** fill the 10 empty `skos:definition`s
    with real LOOK-FOR meanings (and, as a clean-up, split the terse mechanics out of the per-field descriptions
    into the global extraction Skill). Each change measured by its OWN live A/B against the parity baseline, so a
    quality change is NEVER conflated with the faithful migration. This is a standing follow-up, not optional.
- **Phase 2 — load SHACL from the ttl at runtime.** `symbolic_validate` loads the ttl's `sh:` shapes via pyshacl;
  delete Python `FUNCTION_APPLICABLE_DIMS` / `_shapes_graph`. Gate: the contract cascade behaves identically on a
  fixture set, now driven by the ttl.
- **Phase 3 — the requirement side (folds in the deferred INGEST-NS Gaps 1 & 2).** `compliance_bridge.ttl` becomes
  authoritative + complete; requirement applicability dimensions come FROM the ontology (Gap 2: dimension-general,
  not `claim_type`-locked); a ttl-authored SHACL/cue gate validates requirements before the KG write (Gap 1:
  replaces the `"definition" in heading` keyword hack with a deontic-cue validity shape). Gate: a non-advertising
  customer policy ingests with real constraints + is symbolically validated.
- **Phase 4 — migrate the DEON query-side rules to the ontology.** `SECTION_RULE_SCOPE`, `_ACTOR_SYNONYMS`,
  claim-type overrides → the ontology (synonyms as `skos:altLabel`; FTC overrides as a domain-pack ttl), so the
  query side is ontology-driven too — consistency with Rule 1. The mechanism (`deontic_route`, the gates) stays code.
- **Phase 5 (SEPARATE later program, its own ADR) — domain-pack retargeting.** Decouple the KG schema + edge-types
  + entity resolution (EDGAR CIK) from contracts/CUAD/EDGAR so a new domain is onboarded by supplying an ontology
  pack, not editing `store/arcadedb.py` / `entity_resolution.py`. Larger; explicitly sequenced last.

## Consequences

- **The ontology becomes load-bearing and the engine becomes genuinely retargetable** (Phase 5) — a new customer
  domain is a `.ttl` pack, not an engine edit. This is the open-core / neuro-symbolic promise made real.
- **One source of truth, zero drift by construction** — the three-way spec/ttl/Python tangle collapses to one.
- **The prompt-engineering that ADR-0037 protected is preserved** — as a separate overlay, not by holding the
  ontology hostage.
- **Scope is large and touches core files** — `contracts/property.py`, `ontology/*`, `spans/symbolic_validation.py`,
  `store/arcadedb.py`, `capabilities/requirement_extraction.py`, and (Phase 4) `subgraphs/compliance_check.py`. It
  is phased so each step is independently gated and reviewable; no big-bang.
- **A build-time generation step + a CI drift-check are added** — the cost of doing it right; both are standard.

## Settled decisions (review approved 2026-08-31)

1. **The `.ttl` is THE single source of truth** (RDF/OWL/SHACL — the standard, ARD/FOLIO/ODRL-native).
   `contract_bridge.spec.yaml` is retired (or regenerated FROM the ttl); it is never a second hand-editable source.
2. **Generation: build-time codegen + CI-diff for the Pydantic types** (they are static types imported across the
   codebase — runtime-only construction would be fragile), and **runtime pyshacl for the SHACL shapes** (the
   symbolic layer reads the symbolic artifact directly).
3. **No separate overlay layer** (REVISED 2026-09-01, superseding the earlier "YAML overlay" choice). The
   field-level "tuning" in `clause_template.py` was audited and found to be mostly KNOWLEDGE the ttl was simply
   missing (Rule 1): the LOOK-FOR meanings, value examples, and normalizer synonyms are ontology knowledge, so
   they move INTO the ttl (`skos:definition`, `skos:example`, `skos:altLabel`) and generate into the Pydantic
   field descriptions / examples / the normalizer's synonym map. What remains is a handful of GLOBAL extraction
   MECHANICS ("output the canonical value, terse, one per field") — these are prompt text, not per-field config,
   so they live in the extraction PROMPT authored as a **Skill** (`SKILL.md`), the same way every other LLM
   instruction in this system is authored — NOT a bespoke `Class.field` YAML. The one deliberate deviation
   (`document_reference`) stays a hand-carried extraction-mechanism exception. Future: formalize the whole
   extraction prompt as a `SKILL.md`. Rationale (product owner): if the residual is instructions, not data, a YAML
   config is the wrong shape — put instructions in the prompt/Skill; and "the ttl can't express it" is a signal to
   ENRICH the ttl (Rule 2), not to park knowledge in a config file.
4. **Sequencing:** land Phase 0–2 (the contract ontology substrate) BEFORE Phase 3 (the requirement side / the
   deferred INGEST-NS Gaps 1 & 2), so the requirement work is built on the ontology-driven substrate, never
   Python-first-then-redone.
5. **Phase 5 (domain-pack retargeting: KG schema + edge-types + EDGAR-CIK entity resolution)** is a SEPARATE later
   program with its own ADR — sequenced last, not part of this one.
