# ADR-0064: Typed properties travel out-of-band on EvidenceItem (not in the evidence text)

Status: Accepted (2026-08-30)
Date: 2026-08-30
Component: `subgraphs/intra_document_qa.py` (`_clause_to_evidence`, new `_humanize_properties`),
`capabilities/answer_generator.py` (`EvidenceItem.properties`). Raised by: RuleWright (product), engine issue 0011.
Related: ADR-0054 (function label removed from evidence — the same move, one field over), ADR-0055 (confidence
out-of-band as a hedging directive), engine issue 0002 (the paraphrase surface).

## Context

Engine issue 0011. A contract answer narrated the engine's internal typed-property representation to the reader:
"… as indicated by the typed property `cap_quantum=12_months; cap_basis=multiple_of_fees`." `cap_quantum` /
`cap_basis` are schema dimension names — engine vocabulary a reader cannot interpret.

Root cause: `_clause_to_evidence` concatenated the properties into the evidence **text** as `[dimension=value; …]`,
and `_evidence_block` feeds that text straight to the generator. The output scrub (`_PROSE_ANNOTATION_RES`) already
had a rule for the bracketed form — but the model did not emit brackets. It **paraphrased** the annotation into
prose and re-quoted it in backticks. A scrub is a syntax filter; the leak arrived in a syntax it had not been told
about. This is the same failure ADR-0054 fixed for the function label: an annotation placed in the evidence text
gets narrated. The function label was removed; the properties were left in, and leaked the same way.

## Decision

Apply the ADR-0054 treatment to typed properties, but **out-of-band rather than dropped** (they do real work —
the product renders them as UI chips, and they ground the answer):

1. `EvidenceItem` gains **`properties: Optional[list[dict]]`** — code-generated `{dimension, value}` (deterministic,
   no LLM authors it).
2. `_clause_to_evidence`: when the clause has span text, **`text = body`** (properties NOT appended); the structured
   facts ride on `EvidenceItem.properties`. `_evidence_block` renders only `text`, so the generator never sees the
   `dimension=value` tokens and cannot paraphrase them — **narration is structurally impossible, not filtered.**
3. Body-less path (properties are the only content): render them as **reader-safe natural text**
   (`_humanize_properties`, snake_case → spaces, no `[`/`=`/backticks) so the clause stays citable, with the
   structured form still out-of-band. A clause with neither body nor properties is still dropped (contentless).
4. The bracket scrub stays as **defense-in-depth** — the structural fix removes the source, the scrub remains for
   any residual bracketed form. We did NOT take the issue's fallback (extend the scrub to backticked/bare/phrase
   forms): that is the whack-a-mole path the issue itself de-prioritized.

Why dropping from the text costs the generator nothing: the body span already states in natural legal language what
the properties encode (`cap_quantum=12_months` is a structured echo of "shall not exceed the fees paid in the
twelve (12) months preceding the claim"). The properties are redundant *for generation*; they are load-bearing for
retrieval ranking and the product's chips, which read them from the structured field, not the prose.

## Consequences

- **The leak is structurally impossible.** Verified live (real Gemma-4): the OLD inline evidence puts
  `cap_quantum`/`cap_basis` in the model's prompt (present=True); the NEW path does not (present=False). No model,
  temperature, or context can surface a token that is not in the prompt. A unit test pins this
  (`test_typed_properties_are_never_rendered_into_the_prompt`: `cap_quantum not in model.prompt`).
- **Live behavioral run (N=30, concurrent):** OLD 0/30, NEW 0/30 on a synthetic single-clause fixture. The
  intermittent literal-token leak (RuleWright observed 1-of-1 in a real multi-clause browser walkthrough) did NOT
  reproduce at N=30 on this reduced fixture, so no leak *rate* is claimed — the structural absence above is the
  guarantee, and it does not depend on a rate. NEW answers ground on the real clause text and cite correctly.
- **`EvidenceItem` gains an optional field** (`properties`), default None, appended last — positional construction
  and every existing caller are unaffected. It is out-of-band by contract: `_evidence_block` never renders it.
- **Body-less citations stay citable** as reader-safe natural text instead of `[dim=value]` (the issue's caveat).
- New public surface: `EvidenceItem.properties`.
