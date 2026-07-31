# ADR-0037: The clause template is authoritative hand-maintained code, not a regenerated artifact

- Status: accepted
- Date: 2026-07-31
- Related: ADR-0033 (unified contract KG), KG-1 (compiled the bridge OWL -> Pydantic template),
  INGEST-REFACTOR (the `document_reference` truncation runaway), `src/rag_wright/ontology/clause_template.py`,
  `contract_bridge.ttl`, `contract_bridge.spec.yaml`.

## Context

`clause_template.py` was generated once (KG-1) from `contract_bridge.ttl` via `docling-graph template
from-ontology`, and has been hand-maintained ever since (field descriptions, `examples`, `_normalize_enum`
validators, `__str__`). There is NO committed regeneration script; regeneration is a manual CLI invocation.

INGEST-REFACTOR forced the question. The `document_reference` field (the docling-graph graph-id, which we
DISCARD -- our clause identity is the deterministic `chunk_id`) was a required free-text field where granite
dumped 400-char verbatim clause quotes, ballooning the JSON past `max_tokens` and truncating the whole record
(silently dropping the clause). The fix is prompt engineering: make `document_reference` Optional with an
explicit "leave null / never quote" instruction, plus `max_length` guards on the other free-text fields.

That exposed a structural truth: **the template fuses two concerns, and only one is expressible in an
ontology/spec.**

1. **Ontology structure** -- entities, fields, types, enums, edges, genuine domain cardinality. Expressible in
   the `.ttl`/spec.
2. **Extraction-prompt engineering** -- the LLM-guiding descriptions, brevity instructions, `examples`,
   `max_length` guards, normalization validators. This is *how you prompt an LLM*, NOT what the domain is;
   it cannot live in an ontology.

Because (2) cannot round-trip, **faithful regeneration is impossible** -- it would always lose the layer that
makes extraction actually behave. And some `.py` choices legitimately **deviate** from the ontology for
extraction reasons: `document_reference` is semantically a required property of a clause, but we make it
optional purely to stop the runaway.

## Decision

**`clause_template.py` is the authoritative, hand-maintained source of truth for the extraction template.** The
`.ttl`/spec are the ontology (domain) source of truth and the one-time bootstrap -- decoupled from being a live
`.py` generator.

- The `.py` carries a header stating it is authoritative, must not be blindly regenerated, and that
  regeneration must hand-port every refinement to a scratch file.
- The extraction template may **deviate from the ontology for extraction reasons**; each deviation is documented
  at the field (see `document_reference`).
- **Do NOT back-port extraction pragmatics into the `.ttl`/spec.** Encoding `document_reference` as "optional"
  in the ontology would stamp a prompt-engineering workaround as a domain fact and corrupt the ontology. The
  `.ttl` stays ontologically honest (a clause has a reference); the `.py` owns the deviation.
- We do NOT build regeneration machinery (two-layer base+override, spec-format extensions for optionality /
  `max_length`): there is no active regeneration, and the prompt-engineering layer cannot be captured anyway.

## Consequences

- The `.ttl`/spec and the `.py` legitimately diverge and are allowed to. The `.ttl` remains valuable as the
  semantic bridge (FOLIO/ODRL/PROV mappings, the graph schema), not as a `.py` generator.
- Template changes (new fields, new constraints, prompt tuning) are ordinary code edits to the `.py`, reviewed
  and tested like any code -- no generation step, no lossy round-trip.
- New CONTRACT corpora reuse this template unchanged (see the corpus-ingest recipe / SKILL-corpus-ingest). A new
  DOMAIN (non-contract) needs its own bootstrapped + hand-maintained template.
- Directly enables KG-TRUNCATION-BACKFILL: the fixed template lives in the `.py`, so re-extraction picks it up.
