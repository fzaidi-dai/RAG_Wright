# Engine issue 0044: `[DOCUMENT SIGNALS]` scaffolding reaches `citation_claim`, so a product shows it as a quote from the user's document

**Raised by:** RuleWright (product), 2026-09-14, during T-6a.1 (subject-class routing).
**Severity:** correctness of the evidentiary contract. Not a crash; a finding's citation is not what it claims to be.
**Path:** the ADVERTISING check (`run_ad_compliance_check`). The generic path is unaffected.

---

## What we see

A real check, run through the product's UI against the advertising route. The finding renders under our
heading **"IN YOUR DOCUMENT"**, which is `ComplianceFinding.citation_claim` verbatim:

> Our joint supplement cures arthritis in two weeks, guaranteed. Regularly $120, now only $39.
> **[DOCUMENT SIGNALS] disclosures present in the document: guaranteed.**

The user pasted only the first sentence. The bolded half is engine scaffolding.

## Where it comes from

`subgraphs/compliance_check.py`:

- `_document_signal_line(claims)` (L250-263) builds `"\n\n[DOCUMENT SIGNALS] " + ... + "."` — DEON-8, the
  ad-level disclosure/evidence union, deliberately rendered **as document content for the obligation
  judge**.
- `build_obligation_pairs_fn` (L266+) appends it to the evidence bundle and makes that the
  `CheckableFact.assertion_text`:

      text = ("\n\n".join(c.assertion_text for c in evidence) or "(empty subject)") + signal_line

- That fact's text is what surfaces as the finding's `citation_claim`.

The docstring is explicit that this is *for the judge*. Nothing marks it as not-for-display, so every
consumer that renders `citation_claim` as a quotation shows it.

## Why it matters to us

**AC-25 is both-sided citation, and it is the evidentiary basis of a finding.** A lawyer or a marketer
acting on a violation needs to see their own words beside the rule. A citation containing text that is not
in their document is not a citation — and it is worse than a missing one, because it reads as verbatim.
It appeared on **every** obligation finding on the ad path in our run.

We are not going to strip it in product code on a guess about its format: a marker we do not own, matched
by a substring, is a silent breakage waiting for the string to change.

## A second, smaller thing in the same place

Even without the signal line, the obligation path's `citation_claim` is the **top-N evidence bundle**
joined by `\n\n`, not one verbatim span — so on that path "the quote" is a synthesised excerpt. That is
pre-existing and affects the generic path too; the `[DOCUMENT SIGNALS]` suffix is what made it visible.
Whatever shape the fix takes, we would like to be able to tell **verbatim span** from **assembled
evidence** without parsing prose.

## What would help

Any of these unblocks us; the first is what we would choose:

1. **Keep the signals off `citation_claim`.** Pass them to the judge through a field that is not the
   citation (a separate `document_signals` on the fact, or judge-side context), so the citation stays
   document text.
2. **A structured flag on the finding** — e.g. `citation_claim_kind: "verbatim" | "assembled"` — so a
   consumer can render assembled evidence differently instead of quoting it.
3. At minimum, **a documented, stable way to separate the two halves**, so stripping is a supported
   operation rather than a guess.

## Reproduction

Curate any policy carrying an obligation (`The firm shall retain engagement records for no less than six
years.` did it), then run `run_ad_compliance_check` over ad copy containing a word the ad extractor treats
as a disclosure (`guaranteed`). Read `report.findings[*].citation_claim`.

Engine at the commit RuleWright consumes as of 2026-09-14.
