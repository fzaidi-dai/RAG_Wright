# Ingestion-side neuro-symbolic review + gap plan (INGEST-NS)

Date: 2026-08-31. Author: engine review (triggered after the DEON arc / issue 0012 made the compliance QUERY side's
typed fields load-bearing symbolic gates). Read-only review of the INGESTION side against the same axiom, then a
scoped plan. Related: ADR-0040 (the contract-side validation cascade), ADR-0065 (the DEON query-side gates),
ADR-0037 (hand-maintained clause template).

## The axiom being tested

Neuro-symbolic: the KG / ontology (symbolic layer) GROUNDS, CONSTRAINS, GATES, and feeds structured context; the
LLM REASONS over that bounded grounded context — never brute-force. Corollary: domain rules and vocabularies
should live in the ONTOLOGY (declarative), so the engine is domain-retargetable without editing engine code.

## Verdict — three axes, kept separate (conflating them hides the gap)

| Axis | Contract ingestion | Policy/requirement ingestion | Query side (DEON) |
|---|---|---|---|
| **Mechanism** — is the LLM gated by a symbolic layer, or trusted? | Strong (ADR-0040 cascade runs per clause) | **GAP — none; extraction trusted to KG** | Strong (deontic/actor gates) |
| **Knowledge location** — rules/vocab in ontology or Python? | Python (`.ttl` decorative) | Python (`.ttl` decorative) | Python (`SECTION_RULE_SCOPE`, `_ACTOR_SYNONYMS`) |
| **Retargetable** — new customer domain w/o editing engine `.py`? | No (CUAD/legal hardcoded) | No (advertising `claim_type` locked) | Partial |

Key honest finding: the engine's actual consistent posture is **typed KG fields are load-bearing and symbolic
gates constrain the LLM (good), but the symbolic RULES live in Python, not the ontology — everywhere, including
the DEON query side we just built.** The `.ttl` is decorative at runtime engine-wide. That is internally
consistent and a deliberate "no ttl drift" choice (ADR-0037/0040), but it means "ontology-retargetable" is
currently aspirational.

## What is genuinely good (do not lose)

- **The contract-side ADR-0040 cascade is real and running**, not staged: every clause passes lexical grounding
  (`spans/property_grounding.py::reground`) -> genuine `pyshacl` SHACL validation (`spans/symbolic_validation.py::
  symbolic_validate`) -> a narrowed Granite semantic judge (`spans/semantic_judge.py`), each downgrading suspect
  assertions to AMBIGUOUS. Extraction is not trusted. (`spans/clause_kg_extractor.py:185,195,209`.)
- **The shared front-end (parse -> chunk -> segment) is genuinely domain-neutral**: `CorpusAdapter` is a clean
  Protocol (`subgraphs/contract_ingestion_pipeline.py:85-90`), the chunk discoverer/summarizer are injected seams
  (`subgraphs/semantic_chunking.py:85-95`), segmentation splits on generic prose structure (`spans/segment.py`).
  This is the pipeline the subject/compliance front-end was derived from — the good half.

## The four gaps (evidence)

1. **Policy/requirement ingestion has NO symbolic gate.** The produce side of the very typed fields DEON relies on
   is ungated: `capabilities/requirement_extraction.py::to_requirements` -> `store.write_requirements` runs only
   Python coercions (off-vocab deontic -> AMBIGUOUS, off-vocab claim_type dropped) and writes the LLM's
   `deontic_type`/`actor`/`scope` straight to the KG, trusted. The contract side runs `symbolic_validate`; the
   requirement side runs nothing. (Asymmetry: `spans/clause_kg_extractor.py` vs `capabilities/requirement_
   extraction.py:72-95` + `store/arcadedb.py::write_requirements`.)

2. **Requirement `applicability_scope` is locked to `dimension="claim_type"` (a closed advertising vocab).**
   `capabilities/requirement_extraction.py:82-83` only reads `claim_types` and only emits `Constraint(dimension=
   "claim_type", ...)`, dropping anything off the advertising list (`_CLAIM_TYPES`). A customer safety/finance/HR/
   privacy policy's real constraints (jurisdiction, employee_class, data_category) are silently unrepresentable —
   even though the query-side `constraint_applies` matcher is already dimension-agnostic. Ingestion cannot feed it
   anything but claim_type. The extraction template bakes the advertising list into the prompt too
   (`skills/requirement_extraction/template.py:28-31`).

3. **The ontology (`.ttl`) is decorative engine-wide; Python is the source of truth, unguarded against drift.**
   No `.ttl` is parsed at runtime anywhere (query side included). SHACL shapes compile from a Python dict
   (`spans/symbolic_validation.py:69-161,206-230` `FUNCTION_APPLICABLE_DIMS`); vocab is Python
   (`contracts/property.py:87-147` `CLOSED_VOCAB`, `contracts/compliance.py:37-48` `ClaimType`); the ~40 KG edge
   types are Python (`store/arcadedb.py:54-95` `_TYPED_DIMENSION_EDGE`). The only "drift guard"
   (`tests/ontology/test_clause_template.py:80-85`) compares Python-to-Python (clause_template enums ==
   CLOSED_VOCAB); the only `.ttl` test just checks it parses. So the `contract_bridge.ttl:18` comment claiming the
   enums "MUST equal" the `.ttl` overstates a guard that does not exist — edit the `.ttl` and nothing fails.

4. **KG schema + entity resolution are hardcoded to contracts + the SEC/EDGAR corpus.** `store/arcadedb.py` bakes
   `Clause`/`Contract`/`PropertyValue` classes + 40 legal edge types; `capabilities/entity_resolution.py` is
   closed-world to EDGAR CIK (`entity_id` IS the CIK, `store/seam.py:22-23`) on the "generic" pipeline's
   resolve/write nodes — a dead assumption for any non-SEC customer.

## Decision (SUPERSEDED 2026-08-31 by ADR-0066)

The initial framing below deferred Gaps 3 & 4 as "strategic." **The product owner rejected that**: the ontology
`.ttl` MUST be the single source of truth (Python-authoritative is not acceptable — it is the hodge-podge risk).
So Gap 3 (ontology as runtime ground truth) is now the FOUNDATION, addressed first, in **ADR-0066** (the ontology
`.ttl` as the single runtime source of truth + the enforced-synchronization rule). Gaps 1 & 2 fold into ADR-0066
**Phase 3** (built ON the ontology substrate, not Python-first-then-redone); Gap 4 (domain-pack retargeting) is
ADR-0066 **Phase 5**, its own later program. **Read ADR-0066 for the live plan; this doc is the review evidence.**

Original framing (retained for the record; do NOT follow the deferral):
- Gaps 1 & 2 = do now (Gap 2 first, then Gap 1).
- Gaps 3 & 4 = strategic deferral. ← REVERSED by the product owner; ADR-0066 makes the ontology authoritative now.

## Task breakdown — Gaps 1 & 2 (each: TDD contract -> failing test -> implement + a LIVE gate)

### INGEST-NS-1 (Gap 2) — dimension-general requirement applicability
- **Contract:** the requirement extractor emits generic `applicability_scope` `(dimension, value)` constraints, not
  only `claim_type`. The advertising `claim_types` stays as the reference-pack specialization (one dimension among
  many), never the only channel. Off-vocab is recall-first (kept, not dropped) — a customer dimension the engine
  has never seen must survive into the KG, because the query-side matcher already handles unknown dimensions
  recall-first (a rule's constraint gates only when the subject carries that dimension).
- **Shape:** add a generic applicability field to `skills/requirement_extraction/template.py` (the LLM infers the
  scope conditions as role/dimension/value, mirroring the DEON-5 subject `actor` role-bias); `to_requirements`
  maps them to `Constraint`s on `applicability_scope`; keep `claim_types -> Constraint("claim_type")` as one case.
- **Live gate:** a non-advertising customer policy (e.g. a workplace-safety rule scoped to a worker class /
  jurisdiction) ingests with its real constraints PRESENT on the requirement, and a query-side check gates on them.

### INGEST-NS-2 (Gap 1) — symbolic validation gate on requirement ingestion
- **Contract:** a requirement is validated by a symbolic layer BEFORE the KG write (the ADR-0040 pattern, adapted
  to requirements — lighter than the clause cascade). Checks: (a) LEXICAL grounding — `requirement_text` is
  grounded in its source section (reuse the subject-side grounding idea); (b) DEONTIC-cue VALIDITY — a section with
  no normative cue (obligation/prohibition/permission) is non-operative -> skipped SYMBOLICALLY, replacing the
  brittle `"definition" in heading.lower()` keyword hack (`subgraphs/compliance_ingestion.py:60,97`); (c)
  WELL-FORMEDNESS — an actor-bound deontic with an empty actor, or a malformed scope, downgraded to AMBIGUOUS with
  the violation as provenance (never silently trusted, never hard-dropped). Layer-3 semantic judge optional/later.
- **Live gate:** a policy with a definitions section + operative rules -> definitions skipped by the cue gate (not
  a keyword), an ungrounded/hallucinated requirement flagged AMBIGUOUS, operative rules written clean.

## NOT in scope here (recorded for the future Gap 3/4 design)

Loading the `.ttl` as the runtime source of truth (SHACL/vocab/edge-types from the ontology, not Python mirrors);
domain-pack retargeting (KG schema + entity resolution decoupled from contracts/CUAD/EDGAR); a real `.ttl`->code
drift guard. These need their own ADR. The compliance ontology `compliance_bridge.ttl` being never loaded
(`contracts/compliance.py:26` `BRIDGE_TTL_PATH`) is part of that move.
