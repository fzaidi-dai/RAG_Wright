# Engine → RuleWright: subject compliance is now one semantic pipeline (final contracts)

**From:** RAG_Wright (engine) · **Date:** 2026-08-30 · **Status:** shipped on `main`, full suite green (1361),
every step live-validated (real docling + tiered OCR + BGE-M3 + Granite + judge). **Supersedes** the earlier
"unify subject preprocessing" note — that draft described an intermediate state and is deleted; use THIS.

---

## TL;DR — what changed

Checking a subject (a pasted text box OR an uploaded document) against a policy KG now runs ONE uniform
pipeline, for both the generic and the advertising paths:

**parse (tiered OCR) → semantic chunk → verbatim/typed per-chunk extraction → structural locator → judge.**

Three things touch your side:

1. **Citations changed shape and are now VERBATIM.** A finding cites the exact source text with a structural
   locator: `doc § 4.2 ¶3: <verbatim>` / `doc § 4.2 · bullet 2: <verbatim>` / `doc: <verbatim>` (see §1).
2. **OCR PARTIAL is a first-class field.** `ComplianceReport.ocr_unreadable_pages` — surface it (see §2).
3. **The typed-property leak (issue 0011) is fixed structurally** — drop any product-side scrub you added (see §5).

Your existing entrypoints keep their signatures (back-compat, §4). The REQUIRED work is §1 and §2.

---

## 1. REQUIRED — `ComplianceFinding.citation_claim` is a new shape, and VERBATIM

Each finding cites the exact assertion it is about, with a structural locator when the document has structure:

```
"{source_doc} § {section} ¶{n}: {verbatim assertion}"        # a paragraph within a section
"{source_doc} § {section} · bullet {n}: {verbatim assertion}"# a list item within a section
"{source_doc} § {section}: {verbatim assertion}"             # a section, no finer element
"{source_doc}: {verbatim assertion}"                          # a structureless subject (a paste / flat text)
```

- The locator (`§ …`, and the `¶n` / `· bullet n` element marker) is **present only when the document has that
  structure**. A plain-text paste has none → `doc: <assertion>`. **Do not assume every finding has a `§`.**
- **The assertion after the `: ` is VERBATIM** — the exact source text (not a paraphrase, not the whole
  section/document). You can highlight it directly in the uploaded document.
- To split: take everything up to the **first `": "`** as the locator prefix, the rest as the verbatim assertion.
- Structured fields are also on the finding's source fact if you prefer them over string-parsing:
  `section: str|None`, `element_kind: str|None` (docling label: `paragraph`/`list_item`/…), `element_ordinal:
  int|None`. But the `citation_claim` string already encodes all of it.

## 2. REQUIRED — surface `ComplianceReport.ocr_unreadable_pages`

New field on `ComplianceReport`:

```
ocr_unreadable_pages: list[int]   # pages the tiered OCR could not read even after VLM escalation ([] = fine)
```

When a scanned subject has pages the OCR (and the VLM) could not read, this is non-empty. A verdict on such a
document may be based on incomplete text, so **when it is non-empty, warn the user** — e.g. "Pages 3–4 could not
be read; results for those pages are incomplete." Do NOT present a clean "compliant" without this caveat when
pages were unreadable. (This is the engine's ENG-1 "never a silent partial" principle, on the compliance side.)

## 3. The entrypoints (unchanged signatures; new optional params)

All four keep their existing call shape; new params are optional:

```python
# generic subject, pasted text:
await run_generic_compliance_verdict(subject_text, source_doc, *, store, judge_model_id, embedder, k=8,
                                     sources=None, extract_model=None)   # -> ComplianceReport
# generic subject, uploaded document:
await run_compliance_document_verdict(doc_name, data, *, store, judge_model_id, embedder, k=8,
                                      sources=None, extract_model=None)  # -> ComplianceReport
# advertising subject (typed-Claim routing), text OR upload:
await run_compliance_check(subject_text=None, source_doc="", *, store, extract_model, judge_model_id,
                           embedder=None, k=5, sources=None, name=None, data=None)   # -> ComplianceReport
# the unified front-end (text= OR name=+data=), if you prefer one call:
await run_subject_compliance_verdict(source_doc, *, store, judge_model_id, embedder, k=8, sources=None,
                                     text=None, name=None, data=None, extract_model=None)  # -> ComplianceReport
```

- `extract_model` is the per-chunk assertion/claim extractor; it **defaults to the production extraction model**,
  so you don't have to pass it. Pass one only to override.
- `sources` (named-policy scoping) is unchanged; an unknown name raises `UnknownComplianceSourceError` (now
  raised **before** any parse/extraction, so it fails fast).

## 4. Behavior notes (mostly no action, but be aware)

- **The verdict now does an LLM extraction step** (per semantic chunk) in addition to the judge. So a check is
  somewhat more model work than before; `extract_model` is the knob. Cost scales with the number of chunks, not
  raw document length (structural chunking; a fully-structured doc makes zero chunk-boundary model calls).
- **A paste is now parsed through docling** (as `.txt`) so it runs the identical semantic pipeline as an upload —
  slightly more work than the old string path, but uniform. No API change for you.
- **`facts_fn` / `sections_fn` are gone.** If you passed either (you likely did not), remove it — there is now
  one semantic producer, no granularity dial.

## 5. Issue 0011 — drop your product-side scrub

Typed-property facts (`cap_quantum=…`) no longer reach the answer generator, so the "typed property" prose leak
is structurally impossible. If you added a product-side regex/scrub for that phrasing, **remove it** — it is
dead weight now.

## 6. What did NOT change

- The four entrypoints' required arguments and return type (`ComplianceReport`).
- `sources` scoping semantics (0007).
- The compliance MCP tools' call surface.
- `ComplianceFinding.citation_requirement` (the policy-side citation) — unchanged.

## Nothing to decide back to us

This is informational + two required product changes (§1, §2). If a `citation_claim` format edge case bites (a
locator you can't parse, a verbatim assertion that's longer/shorter than you expected), send us the example and
we'll adjust the renderer.
