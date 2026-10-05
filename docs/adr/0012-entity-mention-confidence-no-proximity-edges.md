# ADR-0012: EntityMention carries confidence; the spaCy path emits mentions, never proximity edges

> **Partial update (ADR-0121):** point 4 below — pinning `en_core_web_sm` as a `[tool.uv.sources]` wheel-URL
> dependency — is **superseded**. spaCy is now an optional extra (`rag-wright[ner]`) and its model is a runtime
> download (never a declared/direct-URL dependency), loaded via `rag_wright.util.spacy_model.load_spacy_model`. The
> `RAG_SPACY_MODEL` seam and the rest of this ADR stand.

Date: 2026-07-13. Status: Accepted. Contract change (ask-first, approved): `EntityMention` gains a
`confidence` field, and the design rule that graph extraction never writes a relationship edge from
entity proximity — CONTRACTS_WITH comes from signing-party structure only.

## Context

RAC-23 requires each extraction path — contract extraction, the spaCy NER/dependency path, and the LLM
escalation — to produce ontology-conforming facts carrying `chunk_id` and a confidence tag (FR-S.4).
`EntityMention` (T5) had `text` and `entity_type` but no confidence, so the spaCy path — whose reliable
output is NER mentions — had no confidence-bearing fact to emit. One tempting fix was to have spaCy
emit a relationship edge (e.g. two organizations co-occurring in a sentence → `CONTRACTS_WITH`,
tagged `AMBIGUOUS`) so it had "a fact with confidence."

That fix is wrong. Co-occurrence of two organizations in legal text is frequently NON-contractual, and
often the exact opposite of `CONTRACTS_WITH`: "The Company shall not compete with Acme in the Territory"
→ `CONTRACTS_WITH(Company, Acme)` is flatly inverted; so are a governing-law reference, a bank in a
payment clause, an insurer in an indemnity clause, a competitor in a non-compete. Sentence co-occurrence
of two orgs is common and mostly non-contractual, so a proximity heuristic is a false-edge generator
pointed at the one relationship type the graph leg exists to answer.

The `AMBIGUOUS` tag does not rescue it. Graph query (T26) **surfaces** confidence tags as evidence
(FR-C.5 / FR-Q.3: "treated as evidence, not truth") but does **not filter or down-weight edges by
confidence during traversal**; the only stage specified to act on confidence is the answer generator
(FR-Q.6 / T29), and only softly (evidence-not-truth, no hard gate). So an `AMBIGUOUS` edge is traversed
and reaches the reasoner regardless. Nor does any downstream stage validate relationships: T23b
canonicalizes *mentions* and T24 links them to CIKs; neither filters edges. A false edge that enters
the graph stays in the graph. This is the same error class as ADR-0004's conservative-merge bias — a
false edge, like a silent merge, is worse than an omission because the human cannot see it.

## Decision

1. **`EntityMention` gains `confidence: ConfidenceTag`.** A mention is an ontology-conforming graph
   fact read directly from the text, so a spaCy ORG/PERSON mention (or a contract-extracted party
   mention) is `EXTRACTED`. Its `chunk_id` provenance is the containing `ExtractionResult.chunk_id`
   (mentions are anchored by the result, not individually provenanced, since resolution collapses many
   mentions to one node). This satisfies RAC-23 precisely: the spaCy path produces ontology-conforming
   facts with chunk_id provenance and confidence — those facts are **mentions**.
2. **The spaCy path emits mentions only — never edges.** No proximity/co-occurrence relationships.
3. **`CONTRACTS_WITH` comes from signing-party structure**, via the contract extractor: parties to a
   contract contract with each other — a structural fact of near-perfect precision, exactly what the
   T10 relational golden set encodes (a hub plus its verified co-parties). If a co-occurrence signal is
   ever wanted, it may exist only as a candidate flagged for human verification, never as an edge
   written into the graph.
4. **spaCy model: `en_core_web_sm` (MIT, commercially clean)**, pinned as a uv dependency from its
   release wheel URL (not a `spacy download` side effect, so builds are reproducible and the uv-only
   rule holds). The model name is config-driven behind the injected pipeline seam
   (`RAG_SPACY_MODEL`, default `en_core_web_sm`), swappable to md/lg/trf without a code change (SPEC §17
   domain portability). **Not `en_core_web_trf`:** an OntoNotes-trained NER is noisy on legal text (it
   tags "LLC" as an org, role artifacts, etc.) — that noise is expected and handled by the architecture
   (LLM escalation for hard cases, T23b rejects/canonicalizes, the human gate verifies), not something
   to chase with GPU at the extraction stage.

## Consequences

- `EntityMention` now requires `confidence`; the T5 extraction-contract tests were updated. No other
  contract changes; `RelationshipFact`/`ClauseFact` already carry provenance + confidence via `GraphFact`.
- Graph extraction (T23) proceeds with three extractors behind the T5 seam: `SpacyNerExtractor`
  (mentions only, EXTRACTED), `ContractExtractor` (clause facts + party mentions + party-structure
  CONTRACTS_WITH, EXTRACTED), `LlmEscalationExtractor` (hard-case relationships, INFERRED).
- Recorded for downstream: because T26 surfaces-but-does-not-filter confidence, extraction must not emit
  facts it cannot stand behind. AMBIGUOUS is for genuinely ambiguous *readings of real facts*, not a
  disclaimer stapled to guesses.
