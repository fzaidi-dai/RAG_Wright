# Engine issue 0043: a Requirement carries no page provenance, so a finding can point into the subject but not into the policy it cites

**Raised by:** RuleWright (product) · **Date:** 2026-09-14 · **Severity:** medium — the compliance side
cannot reach the trust moment the contract side already has
**Affects:** `contracts/compliance.py::Requirement` · `subgraphs/compliance_ingestion.py::run_compliance_document_ingestion`
**Not blocking:** we are shipping the page-less version now (below). This asks for the upgrade.

---

## Summary

A `Finding` carries **both sides** of a judgment — `subject_quote` (the span in the customer's document)
and `requirement_quote` (the clause in the policy). That pairing is what makes a finding auditable rather
than merely legible: a reader checks the rule *and* what it was applied to.

We can put the reader in front of the first. We cannot put them in front of the second.

- A **contract span** carries `pages` and a best-effort `bbox` (engine 0032), so our preview opens the
  document at the right page and draws a rectangle over the clause. Shipped at T-4b.4.
- A **`Requirement`** carries `citation` — *"section / paragraph, the human-readable provenance, always
  present"* — and **no pages, no bbox**. So a finding that cites `§ 255.5` can open the policy document
  and say nothing about where in it to look.

**We would like `pages` on a Requirement, with `bbox` best-effort**, in the same shape span provenance
already uses — so the same product code path serves both sides.

## Why `pages` matters more than `bbox` here

Measured on our own corpus (T-4b.4a): an OCR'd scan carried **0 of 3** bboxes but **3 of 3** pages; a clean
born-digital PDF carried 158 of 190 bboxes and **190 of 190** pages. A page is nearly always knowable; a
rectangle often is not.

**Policies are disproportionately scans.** A regulation or a client's standard arrives as a PDF of a
printed document far more often than a contract does. So if only one of the two is cheap to record, pages
is the one that would carry this feature.

## What we are NOT asking for

- **Not `citation` replaced.** It is the right human-readable provenance and our UI shows it regardless.
  This is additive.
- **Not text matching.** We considered locating the requirement by searching the rendered PDF for the
  citation string and rejected it, for the reason engine 0032 made unnecessary on the contract side: it
  fails **silently** on a scan, where there is no text layer to search, and puts the reader in front of the
  wrong text with no signal that anything went wrong. We would rather show nothing than guess.
- **Not a new store shape**, if `pages`/`bbox` on the requirement row fits what `compliance_ingestion`
  already has from the parse. If it does not, we would rather hear that than have it forced.

## What we are shipping meanwhile (so you can see the shape it would slot into)

Page-less: the finding opens the policy document, shows `citation` beside the rule, and claims no position.
The surface does not change when provenance arrives — the finding already opens the policy; it would simply
gain a mark, exactly as the contract side behaves today for the 32 of 190 spans that have no rectangle.

## How we would verify it

Ingest a policy, take a `Requirement` from a finding, and assert its `pages` land on the page whose
rendered text contains its `requirement_text`. Plus the scan case: a policy ingested from a scanned PDF
still reports pages, with `bbox` absent rather than fabricated.

## Our position

Not blocked, and this is not urgent. It is the difference between a compliance finding a lawyer can verify
in two clicks and one they verify by reading a regulation until they find the section — which is the same
gap engine 0032 closed for contracts.

Reference: engine 0032 (span provenance), our T-4b.4a/c/d (how we consume it), `contracts/compliance.py`
(`Requirement`, `Finding`), `subgraphs/compliance_ingestion.py`.
