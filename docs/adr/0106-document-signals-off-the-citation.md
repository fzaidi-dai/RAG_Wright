# ADR-0106: judge-only document signals stay off the citation; verbatim vs assembled is flagged

**Status:** accepted · **Date:** 2026-09-14 · **Resolves:** engine issue 0044 · **Related:** DEON-8 (ad-level disclosure signals), DEON-1/2 (obligation judged once over an evidence bundle), FR-Q.6 (both-sided citation)

## Context

On the advertising obligation path, `build_obligation_pairs_fn` appended a rendered DEON-8 signal line — `"\n\n[DOCUMENT SIGNALS] disclosures present in the document: guaranteed."` — to the evidence bundle and made the whole string the `CheckableFact.assertion_text`. `assemble_finding` builds `citation_claim` as `"{source_doc}: {assertion_text}"`, so the scaffolding surfaced as the finding's citation — which a product renders under "IN YOUR DOCUMENT" as a verbatim quote. It appeared on every obligation finding on the ad path. A citation containing text that is not in the document is worse than a missing one (it reads as verbatim), and it breaks the evidentiary contract (AC-25). The signal line was deliberately *for the judge* (an obligation is judged once over the whole document, so a disclosure made anywhere must reach it), but nothing separated "for the judge" from "for display."

Secondary, in the same place: even without the signal line, the obligation citation is the top-N evidence bundle joined by `\n\n` — assembled evidence, not one verbatim span. A consumer could not tell the two apart without parsing prose.

## Decision

**Separate judge-only signals from the cited text, and label the citation's shape.**

- `CheckableFact` gains `document_signals: str = ""` — judge-only structured signals, never part of the citation. `build_obligation_pairs_fn` now sets `assertion_text` to the evidence bundle **only**, and puts the DEON-8 signal line in `document_signals`. The judge still sees it: `_base_tail` appends `document_signals`, so the judge prompt is byte-identical to before (the same evidence + signal text), while the citation (`assertion_text`) is pure document text. `document_signals` is `""` for a plain fact, so the generic path is unaffected.
- `CheckableFact` gains `citation_kind: Literal["verbatim", "assembled"] = "verbatim"`, and `ComplianceFinding` gains `citation_claim_kind` (copied from the fact in `assemble_finding`). The obligation bundle is flagged `"assembled"`; every other path defaults `"verbatim"`. A consumer renders assembled evidence differently instead of quoting it as the user's exact words — without parsing prose or matching a marker it does not own.

## Consequences

- `citation_claim` on the ad obligation path is now document text only; the `[DOCUMENT SIGNALS]` scaffolding never reaches display. RuleWright's preferred fix (option 1) plus their secondary ask (option 2, the kind flag) in one change.
- The judge's behaviour is unchanged — the signals reach it verbatim via `document_signals` in `_base_tail`; only the *citation* changed.
- Both new fields are additive with back-compatible defaults: existing callers and the generic path are untouched (`document_signals=""`, `citation_kind`/`citation_claim_kind="verbatim"`). `fact_id` on the obligation bundle now hashes the signal-free bundle text; it is a query-time id (never persisted), so this is inert.
- The generic path's citation was already a `\n\n`-joined subject when it bundles evidence; it now carries a truthful `citation_claim_kind` so a consumer can distinguish a single verbatim span from assembled evidence there too.

Full suite: 1579 passed, 44 skipped.
