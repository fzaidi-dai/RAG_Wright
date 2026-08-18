# ADR-0053: Answer-generation output hygiene — internal annotations never leak into user-facing prose

Status: Accepted
Date: 2026-08-18
Component: `generation` skill / `capabilities/answer_generator.py`
Related: FR-C.9 / FR-Q.6 (grounded, cited generation), ADR-0028 (grounding judge), ADR-0045 (client-side tag
parsing). Raised by: RuleWright (product), engine issue 0001.

## Context

`generate_answer` feeds the model an evidence block containing machine-internal markers that are meant to inform
the model's judgement, never to be shown to a reader:

- inline citation ids `[chunk_id]` (shape `id:idx:hash`),
- `[auto-tag: TYPE]` — the engine's own, sometimes-wrong clause classification (PREC-1a framing),
- `[confidence: EXTRACTED|INFERRED|AMBIGUOUS]`,
- the `[dimension=value; ...]` typed-property string,
- the `[Exception ... (inferred)]` carve-out framing (ADR-0044).

RuleWright, which shows generated answers directly to legal/compliance users, observed two leaks in
`GeneratedAnswer.answer`: raw chunk_ids written into the prose (demo-blocking, looks broken), and — the serious
one — the auto-tag and confidence **narrated as findings** ("this limitation is tagged as a Liquidated Damages
provision and is marked with an AMBIGUOUS confidence level"). The auto-tag is unstable across runs
(`Cap On Liability` → `Liquidated Damages` twice on the identical clause), so narrating it asserts a wrong clause
type in the engine's own voice, beside correct quoted text — a trust/correctness defect, not a cosmetic one.

Root cause is a **skill-wording gap, not a model failure**: the SKILL taught the model not to *trust* the
auto-tag/confidence, but never not to *repeat* them; and the tag protocol asks for inline `[chunk_id]` citations,
which the model duly leaves in the prose. The model did what it was asked.

The product deliberately did **not** paper over this at its own seam: rewriting engine model-output in the
product would mean guessing which bracketed text is safe to remove. The engine knows the exact annotation
formats, so the fix belongs in the engine.

## Decision

Two coordinated fixes, matching the two failure surfaces:

1. **Code scrub (the hard guarantee).** Add `_scrub_prose(text, evidence)`, applied in `_finalize` — the single
   point every generation strategy (single-call, reasoned, best-of-N; intra-document and relational QA) already
   passes through, alongside the existing "drop fabricated citations / coerce uncited to abstention" guarantees.
   *After* citations are extracted, it removes from the answer prose: the exact evidence `chunk_id`s (bracketed,
   and bare for citation-shaped ids), any bracketed `[id:idx:hash]` (so a fabricated id is caught too),
   `[auto-tag: ...]`, `[confidence: ...]`, `[Exception ... ]`, and `[dim=value; ...]` groups; then it tidies the
   whitespace/punctuation left behind. It is **targeted, not a blanket bracket strip** — only the known
   annotation formats and the exact evidence ids are removed, so a legitimately quoted bracket (a defined term
   like `[Party A]`) survives, and quoted clause text is untouched. If scrubbing leaves no readable prose, the
   answer is coerced to an abstention (consistent with no-claim-without-a-citation).

2. **SKILL wording (the paraphrase surface).** A regex cannot catch a paraphrase like "tagged as a Liquidated
   Damages provision". Add an output-hygiene rule to `skills/generation/SKILL.md`: the annotations are inputs to
   judgement, never repeated, named, quoted, or described to the reader; they shape *how confidently* the model
   answers, then it states the substance in plain language; citation ids are recorded separately.

Citation ids continue to live in `GeneratedAnswer.citations`; quoting the clause's real text is still encouraged.

## Consequences

- `GeneratedAnswer.answer` is now guaranteed free of the engine's internal annotation tokens and evidence ids,
  deterministically, for every generation path — the belt (SKILL) reinforced by braces (code).
- The trust defect (a possibly-wrong auto-tag narrated in the engine's voice) is removed at the source; the
  product no longer needs to consider scrubbing engine prose.
- The scrub is deliberately conservative: it does not remove arbitrary bracketed text, so legitimate quoted
  brackets survive. If new internal annotation formats are added to the evidence block later, they must be added
  to `_PROSE_ANNOTATION_RES` (and the SKILL) in the same change, or they will leak.

## Verification

Hermetic tests in `tests/capabilities/test_answer_generator.py` encode the report's acceptance criteria: prose
contains no chunk_id substring and no bracketed annotation groups while `citations` still carries the id and the
real clause quote survives; a legitimately quoted `[Party A]` is preserved; an annotation-only answer abstains.
Generation, intra-document, and relational QA suites pass; ruff clean.
