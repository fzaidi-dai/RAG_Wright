# ADR-0054: Remove the KG function label (`[auto-tag: TYPE]`) from generator evidence

Status: Accepted
Date: 2026-08-18
Component: `intra_document_qa._clause_to_evidence` / `generation` skill
Supersedes: the PREC-1a auto-tag framing in `_clause_to_evidence` (and its silver-fixture migration
`migrate_silver_evidence_autotag.py`). Related: ADR-0053 (answer output hygiene, issue 0001), ADR-0047 (retire
the precomputed function gate), ADR-0044 (exception carve-outs), ADR-0028 (grounding/confidence). Raised by:
RuleWright (product), engine issue 0002.

## Context

ADR-0053 (issue 0001) closed the *mechanical* leak of internal annotations into answer prose with a scrub in
`_finalize`, and added a SKILL rule against *narrating* them. The SKILL rule is a behavioural nudge, not a
guarantee. RuleWright measured the residual paraphrase leak — the model describing an evidence item's
`[auto-tag: TYPE]` classification in prose ("this is tagged as a Liquidated Damages provision") — at 0/55 on
engine `e24eb1a` (rule-of-three upper bound ~5.5%), and reframed the earlier "~8%, no action" as a per-demo
probability: across a handful of questions in a customer meeting, the chance a prospect sees a *wrong clause
type asserted in the engine's own voice* beside correctly-quoted text approaches a coin flip, and one such
assertion teaches the viewer the citations cannot be trusted either. That is the product's whole proposition.

The `[auto-tag: TYPE]` string is the KG's function classification, prepended to the evidence text the generator
reads. It is **generation-only**: produced in exactly one place (`_clause_to_evidence`) and consumed only by the
generator. Retrieval, routing, and ranking use the `clause.function` *field* directly (and ADR-0047 already
retired the precomputed function *gate*), so the evidence-text label is not load-bearing anywhere upstream.
Within generation, the SKILL's method is to **judge each item by its actual text and verify the text
instantiates the concept the question asks** — a text-vs-question comparison that never uses the label. The label
is a value the SKILL then tells the model to *distrust*: it buys the narration risk and contributes nothing to
the method doing the work.

## Decision

**Stop putting the KG function label in the evidence text** (Option A — structural, zero cost, zero latency).
The model cannot narrate what it is never shown, so the wrong-type-assertion failure becomes structurally
impossible rather than probabilistically rare.

`_clause_to_evidence` now renders:
- **body present** (the common case, where every observed leak occurred): the clause's real span text, with its
  typed facts appended as `[dim=value; ...]` — no `[auto-tag: ...]` prefix.
- **facts-only** (no span text): the typed facts alone, `[dim=value; ...]` — the dimension names carry the
  semantics.
- **contentless** (no span text and no facts): **dropped** (returns `None`). Such a clause has nothing to ground
  a citation on; it is no longer cited by a bare function label (superseding PREC-1a's fallback). The caller
  filters out the dropped items; if that leaves no evidence, the generator abstains.

The ADR-0044 inferred-exception frame (`[Exception ... (inferred)]`) and the `[confidence: ...]` tag are
**kept** — confidence does real hedging work (FR-S.4/ADR-0028) and its leak (a true property) is lower-severity
than a misclassification. Confidence stays inline for now; moving it out-of-band is deferred pending measurement.

The SKILL's "treat the `[auto-tag: TYPE]` prefix as a guess" bullet is replaced by the same method stated without
the label ("judge each item by its actual text; verify it instantiates the concept asked; hedge/abstain on
mismatch"). The `_finalize` scrub keeps its `[auto-tag: ...]` pattern as defense-in-depth (nothing should emit it
now).

Option B (layered tripwire/repair/judge at `_finalize`) was rejected: it catches a residual the product already
detects-and-logs, reintroduces cost/latency on a now-rare event, and is unnecessary once the source is removed.

## Consequences

- The high-severity leak (a wrong clause type asserted in the engine's voice) is eliminated at the source, for
  every generation path.
- **Behaviour change:** a property-less clause with no resolvable span text is no longer cited at all (it was
  previously cited via its function label). This is intended — such a citation could not be grounded in text.
- The frozen silver eval fixture (`tests/fixtures/leg_a_silver/evidence_snapshot.json`) is migrated in place by
  `scripts/migrate_silver_evidence_remove_autotag.py` (deterministic, idempotent) so the eval keeps measuring the
  current pipeline; 233 items had the prefix removed, 0 dropped.
- If confidence-paraphrase proves material in RuleWright's planned N=200 run, moving `[confidence: ...]`
  out-of-band is the next lever (not done here).

## Verification

Hermetic tests in `tests/subgraphs/test_intra_document_qa.py`: evidence is the clause's real span text with no
`[auto-tag: ...]` and no function label; a facts-only clause renders `[dim=value]` with no label; a contentless
clause is dropped (returns `None`) and, as the only clause, the QA abstains. Generation, intra-document, and
relational QA suites pass; ruff clean.
