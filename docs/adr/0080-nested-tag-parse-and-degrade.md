# ADR-0080: Nested-schema client-side tag parsing + re-ask-then-omit-to-default degrade

Date: 2026-09-05
Status: Accepted (implemented; TAGPARSE-INGEST-1a)

Extends ADR-0045 (client-side XML-tag structured output). Foundation for moving ingestion extraction off
docling-graph's server-side JSON onto the tag-parse seam (TAGPARSE-INGEST-1).

## Context

ADR-0045's `build_tag_structured` covered FLAT schemas only (scalars, enum/Literal, `str | None`, `list[<scalar>]`)
— enough for the query side. But the ingestion extraction contracts are NESTED: `Clause` has nested-object fields
(`bounded_by`, `caps`, `governed_by`) and a `list[<BaseModel>]` (`excepts`); `ContractParties` has
`parties: list[Party]`. Those hit `NotImplementedError` in the old emitter/parser, so ingestion could not use the
tag-parse path — it stayed on docling-graph's `response_format: json_object`, the server-side-JSON fragility this
program is retiring (see ADR-0079's finding: ~5/7 clause extractions returned "empty or all-null JSON").

A second problem is real-model robustness: a model may emit a PARTIAL nested block (a `<governed_by>` with no
`jurisdiction_name`) or a constraint-violating scalar. Strict validation would fail the whole extraction.

## Decision

1. **Nested schema support by recursion.** `_classify` sorts each field into `scalar` / `list_scalar` / `nested`
   (single `BaseModel`) / `nested_list` (`list[<BaseModel>]`). The emitter (`tag_instructions`) and parser
   (`parse_tagged`) recurse: a nested field emits `<f><sub>…</sub></f>`; a list-of-model emits repeated
   `<item>…</item>` blocks. Parsing scopes each nested body to its own tag, so sub-field name collisions across
   parents are safe.

2. **Degrade = re-ask (strict), then omit-to-default (lenient) on the last attempt.** `build_tag_structured`
   parses STRICT on every attempt but the last — a malformed/partial answer raises `ValidationError` and is
   re-asked (bounded by `retries`). The FINAL attempt parses LENIENT: a NON-required field whose value fails to
   validate (an unbuildable nested block, a constraint-violating scalar, an invalid list item) is omitted to its
   default via `_prune_invalid_optionals`, instead of failing the whole extraction. A missing REQUIRED field still
   raises, so the caller's graceful-degrade contract owns genuinely-unrecoverable cases. Lenient only touches
   nested/failed fields, so flat query-side schemas behave identically.

## Consequences

- **Ingestion contracts are now tag-parseable.** Live on granite-4.2: `ContractParties` extraction 5/5, 0.8–1.6s
  — the exact path that dead-lettered under docling-graph. `Clause` runs without crashing (degrades) — its
  remaining quality issue (a 35-field one-shot template overwhelms the model) is addressed by the
  FUNCTION-INDEPENDENT thematic-group decomposition in TAGPARSE-INGEST-1b, not here.
- **Robustness matches the mandate:** persistent partial output degrades to a usable object, never a hard error;
  the re-ask gives the model one honest chance to fix itself first.
- **No query-side change** (25 tag-parse tests incl. the flat regressions green; full suite green). `_field_kind`
  became the internal `_classify`; all external callers use only `build_tag_structured`.
- **Scope:** nesting depth is unbounded by construction, but the shipped contracts are depth-2 (a nested model
  whose sub-fields are flat), which is what the live tests exercise.
