# ADR-0014: Split FR-C.9 into `generation` and `vision_to_text` (discovery dilution)

> **Status: PARKED (ADR-0052).** A GraphWright / RLM-era decision, parked for now as part of the engine/product split; the referenced capability may still exist in code but is not treated as part of the current supported surface. Revisit or revive if a future need arises.


Date: 2026-07-14. Status: Accepted. Splits the bundled FR-C.9 `generation` capability into two slugs —
`generation` (grounded, cited, abstaining answer generation) and `vision_to_text` (scanned-image
transcription) — so ARD discovery ranks each on its own intents. Approved SPEC change (from GraphWright's
`RegistryStore` verification of the mirror).

## Context

FR-C.9 was registered as one capability, `generation`, bundling three things on the Gemma 4 class model:
cited-answer generation, abstention, and vision-to-text. Its ARD manifest's representative queries
therefore spanned two genuinely different behaviors — "answer a question grounded in the retrieved
evidence with citations" alongside "transcribe a scanned filing image to text."

ARD discovery ranks a capability on those representative queries, so a slug describing two behaviors
matches poorly at both: it surfaces for image-transcription intents it should not serve, and ranks weakly
for the answer-generation intent it should. This is not hypothetical: GraphWright's discovery gate
(T9d.4) has a band where a mediocre score clears the deterministic bind floor but the judge flags it as
`fix-discoverability` — a diluted `generation` lands in exactly that band, and the gap report would tell a
developer to "improve queries" for a capability that already exists and works.

The bundling was a spec convenience (one model, so one capability), not a design decision. Answer
generation and vision-to-text have different inputs (retrieved evidence vs. an image), different callers
(the query path vs. ingestion), and different failure modes.

## Decision

Split FR-C.9 into two capabilities with two canonical slugs:

- **`generation`** — the answer generator: grounded, cited (no claim without a citation), confidence-aware,
  abstention-willing (FR-Q.6). Input: retrieved evidence. Caller: the query path.
- **`vision_to_text`** — scanned-image transcription at ingestion. Input: an image. Caller: ingestion.

Each authors its own ARD manifest with representative queries scoped to its behavior only. Both remain
`kind: function` and run on the GENERAL-role Gemma 4 model via the model-profile seam; the split is about
discovery, not about the model or the binding. `CANONICAL_CAPABILITY_SLUGS`, the SPEC §5 slug list and
FR-C.9, and the T29 ledger entry are updated to match; both manifests are re-emitted to the shared root.

## Consequences

- Discovery ranks `generation` cleanly on answer-generation intents and `vision_to_text` on transcription
  intents; neither dilutes the other, and the gap report no longer mislabels an existing capability as
  needing better queries.
- The manifest count in the shared root goes from 13 to 14 (`vision_to_text` added; `generation`
  re-scoped). GraphWright re-verifies the mirror after the re-emit.
- Precedent recorded for the ARD scope note (SPEC §5): a canonical slug and an ARD manifest are distinct;
  the manifest rule is "discovered by query at compile time," which yields three categories
  (query-discovered → manifest; seam-bound nodes → neither; foundation derivations → slug, no manifest).
