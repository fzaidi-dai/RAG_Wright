# Engine → RuleWright: unified subject preprocessing + section-precise citations (shipped)

**From:** RAG_Wright (engine) · **Date:** 2026-08-30 · **Re:** engine issues 0010, 0011, and the
"unify subject preprocessing" arc (UNIFY-A..F) · **Status:** shipped on `main`, full suite green (1345),
each step live-validated (real docling parse + real BGE-M3 + real judge).

---

## TL;DR

Three things shipped that touch your side:

1. **0011 (done):** typed-property facts no longer leak into answer prose — the fix is structural (properties
   travel out-of-band, the generator never sees the `dimension=value` tokens), so no product-side scrub is needed.
2. **0010 (done):** a compliance finding now cites the **sentence** it is about, not the whole subject.
3. **UNIFY (done):** ONE subject-compliance front-end that takes **either pasted text or an uploaded document**
   (PDF/DOCX/HTML/TXT), and findings now cite **`doc § {section}: {sentence}`** — section from structure,
   sentence from segmentation. This also lit up **upload support for the advertising path**.

Your existing calls keep working (back-compat preserved). The one thing you **must** check is how you parse
`ComplianceFinding.citation_claim` — its format changed (see §1 below).

---

## 1. REQUIRED: `citation_claim` format changed — parse it tolerantly

`ComplianceFinding.citation_claim` is now one of two shapes:

```
"{source_doc} § {section}: {sentence}"     # structured subject (upload with headings / numbered sections)
"{source_doc}: {sentence}"                  # structureless subject (a plain-text paste) -- unchanged shape
```

- The ` § {section}` segment is **optional** — present only when the subject had structure (a parsed document),
  absent for a pasted text box.
- If you display or split `citation_claim`, split on the **first `": "`** to separate the locator prefix from the
  sentence; the prefix is `"{source_doc}"` optionally followed by `" § {section}"`.
- Do **not** assume every finding carries a `§`.

There is also a new optional field upstream if you consume `CheckableFact` directly: `CheckableFact.section:
str | None` (the locator; `None` for a structureless subject). You generally won't need it — it's already folded
into `citation_claim`.

## 2. RECOMMENDED: migrate to the one unified entrypoint

New: **`run_subject_compliance_verdict`** — the single front-end for checking any subject (paste OR upload):

```python
await run_subject_compliance_verdict(
    source_doc,                      # a name/id for the subject
    store=..., judge_model_id=..., embedder=...,
    sources=[...],                   # 0007: scope to named policies (unchanged)
    # exactly ONE of:
    text="pasted subject text ...",  # a text box
    # OR
    name="subject.pdf", data=<bytes>,  # an uploaded document (PDF/DOCX/HTML/TXT)
    # optional:
    facts_fn=...,                    # granularity override (sections -> facts); default = per-(section, sentence)
)  # -> ComplianceReport
```

- **Upload** (`name`+`data`): parsed (docling, async-bounded, tiered OCR from 0009) → heading-split → judged
  per (section, sentence). Findings cite `doc § {section}: {sentence}`.
- **Paste** (`text`): short-circuited to one section (no docling round-trip). Findings cite `doc: {sentence}`.

## 3. BEHAVIOR CHANGE if you call `run_compliance_document_verdict` (uploads)

It still exists (now a thin shim), but its **default granularity changed from per-section to
per-(section, sentence)**:

- You will get **more, finer findings** — one per sentence per applicable requirement, each citing its sentence.
- Any UI that assumed *one finding per section* needs updating.
- To keep the **old per-section** behavior exactly: pass `facts_fn=document_facts_fn`.

`run_generic_compliance_verdict(text, source_doc, ...)` (the text path) is **unchanged in behavior** (per-sentence,
no `§`) — it's now a shim over the unified entrypoint, but output is identical.

## 4. NEW: the advertising path accepts uploads

`run_compliance_check` (the ad path) now accepts an uploaded ad, not just a string:

```python
await run_compliance_check(                      # paste (unchanged):
    ad_text, source_doc, store=..., extract_model=..., judge_model_id=..., embedder=..., sources=[...])

await run_compliance_check(                      # NEW: upload
    source_doc="flyer.pdf", name="flyer.pdf", data=<bytes>,
    store=..., extract_model=..., judge_model_id=..., embedder=..., sources=[...])
```

- Typed-`Claim` extraction (claim_type, disclosures, routing) is **unchanged** — it's still the tail.
- An uploaded ad is parsed and claim-extracted **per section**; ad findings now also cite `§ {section}: claim`.
- The paste form and the MCP ad tool are unchanged and compatible.

## 5. No change needed

- `run_generic_compliance_verdict(text, source_doc, ...)` — identical output.
- `run_compliance_check(ad_text, source_doc, ...)` — identical output (paste form).
- The compliance MCP tools — internally updated, same call surface.
- 0011 (typed-property leak): fixed engine-side, structurally — you can **drop any product-side scrub** you added
  for `cap_quantum=...`/"typed property" phrasing; the generator no longer sees those tokens.

## Open question for you

For uploaded documents, do you want the **per-sentence** default (finer, more findings, sentence-precise
citations) or the **per-section** rollup (`facts_fn=document_facts_fn`)? The engine defaults to per-sentence;
tell us if the product UX wants section rollup and we'll make that the product-facing default.
