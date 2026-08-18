# ADR-0055: Confidence delivered out-of-band as a hedging directive, not an inline evidence marker

Status: Accepted
Date: 2026-08-18
Component: `capabilities/answer_generator.py` / `generation` skill
Related: ADR-0053 (answer output hygiene, issue 0001), ADR-0054 (remove auto-tag from evidence, issue 0002
Option A), ADR-0028 (grounding/confidence cascade), FR-S.4 (provenance/confidence). Raised by: RuleWright
(product), engine issue 0002 (final section, N=200 measurement).

## Context

ADR-0054 removed the KG function label from generator evidence; RuleWright's N=200 run on engine `68e4e62`
confirmed **zero auto-tag narration** (the dominant symptom, gone). The residual leak was the **confidence
marker**: the `[confidence: EXTRACTED|INFERRED|AMBIGUOUS]` tag was rendered inline in the evidence block
(`_evidence_block`), and the model narrated it to the reader — "tagged with a confidence level of AMBIGUOUS".

The measurement showed why this matters more than its headline rate: **17% of runs, but ~100% conditional.**
Only one of six questions leaked, and it leaked on essentially every run, because it cites the one clause
carrying a non-`EXTRACTED` tag. So the rate is not "17% of answers" — it is "almost every answer that cites a
clause the engine is unsure about." The clauses the engine is least certain about are exactly the interesting
ones a prospect asks about in a demo, so the narration surfaces precisely when it does the most damage, and it
is self-defeating: an internal uncertainty signal meant to make the answer *hedge* instead became a sentence
telling the reader the engine is unsure, in the engine's own voice.

Confidence genuinely does hedging work (FR-S.4 / ADR-0028), so it cannot simply be deleted like the auto-tag.

## Decision

**Move confidence out-of-band** (RuleWright's recommended lever, the same move that worked for the auto-tag).
Instead of an inline `[confidence: ...]` marker sitting in quotable evidence text, the worst-case certainty
across the evidence becomes a **hedging directive the prompt consumes** — a tone instruction appended after the
evidence, never quotable:

- `_evidence_block` no longer renders any `[confidence: ...]` marker; the block is just `[chunk_id] text`.
- `_confidence_directive(evidence)` returns a short "Certainty note (do NOT mention to the reader)" appended to
  the prompt when any evidence is `AMBIGUOUS` (be tentative, do not state as settled) or `INFERRED` (present as
  an inference); empty when all evidence is `EXTRACTED`/plain. Applied in the single-call and reasoned prompts.
- **The directive does not name the internal enum tokens** (`INFERRED`/`AMBIGUOUS`), and the SKILL no longer
  contains them either, so the raw tokens never appear in the prompt and cannot be echoed. This makes the leak
  structurally closed, not merely nudged — consistent with ADR-0054.

The confidence data (`EvidenceItem.confidence`) is unchanged; only its *delivery* changed. Per-item precision is
traded for an aggregate worst-case directive — accepted, as the goal is hedging tone, and RuleWright endorsed the
directive approach.

**Secondary fix (same issue):** the model sometimes wrote a literal schema *field name* (`[chunk_id]`) where a
citation would go — engine vocabulary, not an id, matched by neither the id-scrub nor the annotation scrub. Added
`[chunk_id|clause_id|source_doc_id|span_id|answer_kind]` to `_PROSE_ANNOTATION_RES` so those literals are
stripped from the prose.

## Consequences

- The confidence-paraphrase surface is closed at the source: no quotable marker, no raw token in the prompt.
- Hedging is preserved as an explicit tone directive; the SKILL's confidence bullet now reads "hedge according to
  the certainty note, if one is given" rather than referencing an inline tag.
- Aggregate (worst-case) hedging is coarser than per-item: if evidence mixes certain and uncertain items, the
  directive nudges tone for the whole answer. Acceptable for the hedging goal; per-item delivery (e.g. keyed by
  chunk_id, which is scrubbed from prose anyway) is a possible future refinement if measured to matter.
- No API/contract change (`GeneratedAnswer`, `EvidenceItem` unchanged).
- RuleWright separately reported that the facts-only citation shape (ADR-0054) interacted with a product rule and
  fixed it on their side (facts-only citation kept with empty text + populated facts); no engine change requested.

## Verification

Hermetic tests: the evidence block carries no `[confidence: ...]` marker; the raw enum token never appears in the
prompt; an `AMBIGUOUS`/`INFERRED` item adds the hedging directive while `EXTRACTED` adds none; a literal
`[chunk_id]`/`[clause_id]` in prose is scrubbed. Subgraphs + capabilities suites pass (566/17 skip); ruff clean.
RuleWright to run N=200 against `main` to confirm the rate.
