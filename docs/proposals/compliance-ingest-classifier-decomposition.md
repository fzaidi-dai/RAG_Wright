# Compliance-ingest classifier decomposition — cut the per-section LLM cost (the Step-2/Step-3a analog)

Status: PROPOSAL (2026-10-04). Scope: engine-side reference pack. Deferred out of EP-REF-1c
("compliance-side classifiers to cut ingest LLM calls = deferred"); this is that work, specced.

> One-line: compliance requirement extraction is the **un-decomposed analog of the OLD contract per-provision
> extraction**. Decompose it the same way contracts were (deterministic boundaries + cue-rules + closed-vocab
> classifiers + verbatim text, with at most one small residual LLM call), ttl-driven so it covers every domain pack.

---

## 1. The finding (grounded)

Per operative section, compliance ingest runs a docling-graph **`auto`/`dense` multi-LLM pass**
(`capabilities/requirement_extraction.py::extract_regulation_section`) that does *both* jobs the contract
pipeline long since split apart:

1. **finds the distinct rules** in the section — `ExtractedRegulationSection.requirements: list[ExtractedRequirement]`
   (one edge per rule), and
2. **fills every field** per rule — `deontic_type`, `actor`, `claim_types`, `applicability`, `evidence_standard`,
   and a **paraphrased** `requirement_text` (a single generated sentence, NOT a verbatim span).

Contrast with contract ingest (`docs/contract_pipeline_explainer.md`):

| | Contracts (decomposed) | Compliance (today) |
|---|---|---|
| Unit boundaries | **deterministic** (section numbering / headings, Step 2) + a soft clause-type classifier | LLM finds the rules inside the `auto`/`dense` pass |
| Closed-vocab fields | **classifiers** (Step 3a: 21 dims) | LLM fills `deontic_type` / `actor` / `claim_types` / `applicability` |
| Open / numeric fields | **1 LLM call / provision** (7 numeric/open fields) | folded into the same per-section pass |
| docling-graph | **once per contract** — party/graph structure only (Step 3c, cached) | run **per section**, `auto`/`dense`, as a reader |

docling-graph is the SAME `dg_extraction` machinery in both; contracts use it once per contract for structure,
compliance runs it per-section with `auto`/`dense` so it also does rule-finding + tagging. On a 100-page
regulation with many sections × dense multi-call, this is the "thousands of LLM calls" ingest cost — the exact
shape the contract Step-2/3a refactor removed.

**The deontic-cue machinery already exists and is ttl-driven.** `is_operative`
(`subgraphs/compliance_ingestion.py::_deontic_cue_pattern`) gates sections today by the cues authored in
`compliance_bridge.ttl` (`cmp:cue` per deontic type, via `ontology/loader.py::load_deontic_cues`). It is
currently **section-level**; the decomposition extends it to **span-level**.

(Cost/volume and classifier-training-time reasoning are deliberately OUT of this assessment — not the question.
The question is purely: where are ingestion-time LLM calls doing work a deterministic rule or a local classifier
can do, analogous to contracts' Step 2 and Step 3a.)

---

## 2. The decomposition (ingestion) — mirror the contract pipeline

**A. Rule-span segmentation — the Step-2 analog (biggest single reduction).**
Split each operative section into candidate rule-spans **deterministically** — regulations carry paragraph /
enumeration structure (`(a)`, `(b)`, `¶`, sub-sections) just like contract numbering — then gate each span by the
existing deontic-cue check (extend `is_operative` from section- to span-level). A small **binary
"is this span an operative rule?" classifier** (the analog of contracts' `is_extractable_span`) handles spans
without a clean cue and drops definitions / headings / cross-references. This removes the LLM's
"find the distinct requirements" job.

**B. `requirement_text` → verbatim span, not a paraphrase.**
The template paraphrases each rule into one sentence today. Quote the identified span **verbatim** instead
(as `assertion_extraction` already does) — more faithful for citation and it drops a generative step. An optional
short paraphrase stays available but is not needed for the decision/record.

**C. Per-rule closed-vocab field tagging — the Step-3a analog.**
Over each identified span:

- **`deontic_type` (3-way) → a cue RULE, no ML, ttl-driven** — the cues are already authored (`cmp:cue`); map the
  matched cue → type. Cheapest, do-now. (Off-cue → `AMBIGUOUS`, preserving the current graceful-degrade contract.)
- **`actor`-role (~small closed set) → classifier.**
- **`claim_types` (8-way, multi-label) → classifier.**
- **`applicability` dimensions (closed vocab) → classifier** — the direct analog of the contract property-dimension
  fleet.
- **`evidence_standard`** — the one genuinely open/rare field → keep as the **single residual LLM call per
  section** (mirrors contracts' "7 numerics → 1 LLM call/provision"), or classify if it turns out closed.

**Net:** the per-section `auto`/`dense` multi-LLM extraction → **deterministic/classifier segmentation +
classifier field-tags + verbatim text + at most one small residual LLM** — the same N-calls → ~1 shape the
contract Step-2/3a refactor achieved.

---

## 3. ttl-driven, so it covers every pack — and the ads special case

The mechanism is **domain-agnostic** (ADR-0066/0117): it reads cues + closed vocab from the **loaded pack
`.ttl`**, never from Python literals. Two consequences:

- **The no-ML slice (A + B + the `deontic_type` cue-rule) is generic and transfers to any pack for free** — it
  reads whatever cues/vocab the pack authors.
- **The trained field-classifiers (actor / claim_types / applicability) are vocabulary-specific.** A classifier
  trained on one pack's vocab does not transfer to a different vocabulary. This is the per-pack-classifier model:
  the engine builds the **mechanism + the seam + the reference-pack classifier**; a genuinely new domain pack
  trains **its own** classifiers against the same seam (product / domain-pack side).

**The FTC ads "ttl-driven compliance" is NOT a new-vocabulary case.** `ontology/packs/ftc_16cfr255.ttl` contains
only `cmp:SectionOverride` instances (per-section rule-scope + applicable-claim-type re-routing); it authors no
deontic cues, claim-type vocab, actor roles, or applicability dims — it reuses the shared `compliance_bridge.ttl`.
An FTC policy therefore **ingests through the identical path with the identical vocabulary**, so CIC-0/CIC-1 cover
its ingestion with no ads-specific work. The ads specialness is **query-side** (the section overrides +
`run_ad_compliance_check`), which this proposal leaves as a query-time LLM concern (§4). So ads needs **no separate
ingestion arc** — only the general per-pack note above (CIC-2) applies, and ads is explicitly not an instance of
it.

---

## 4. Query-time (out of scope here — live with the LLM, as contracts do)

- **Subject assertion/claim extraction** runs at *check* time and already uses the lighter `direct` (single-pass)
  extraction, not `auto`/`dense`. Leave it.
- **The judge** (`compliance_judgment.py`) is query-time. Its verdict *is* a 3-way pair-classification
  (compliant / violation / needs_review over a (claim, requirement) pair), so a cross-encoder / NLI typed-decision
  is a real candidate — verdict from a classifier, LLM reserved for the rationale or low-confidence fallback. But
  it is query-time and semantic, so — exactly as we accept query-time LLM on the contract side — leave it for now.
  Not an ingestion item.

---

## 5. Tasks (filed in `docs/specs/engine-platform/TASKS.md`, section "CIC")

- **CIC-0 (do-now, no-ML slice):** deterministic sub-section rule-span splitting + span-level `is_operative` gate +
  `deontic_type` cue-rule + verbatim `requirement_text`; residual single LLM call per section only for the open
  field(s). Generic mechanism reading the loaded pack `.ttl`. Live A/B on 1–2 real policies (requirement recall +
  field agreement vs the current per-section dense extraction).
- **CIC-1 (follow-on):** per-vocab closed-vocab classifiers (actor / claim_types / applicability), distilled from
  the current LLM extractor's labels, behind the capability/model-profile seam, reference-pack vocab. Replaces the
  residual LLM field-fill for closed dims.
- **CIC-2 (standing per-pack note, not engine work here):** a new-vocabulary domain pack trains its own
  CIC-1-style classifiers against the seam. FTC ads is NOT such a case (reuses the shared ingest vocab; differs
  only at query time).
