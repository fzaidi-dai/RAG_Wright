# ADR-0063: Per-sentence compliance subject facts (the `facts_fn` seam; sentence segmentation as the default)

Status: Accepted (2026-08-30)
Date: 2026-08-30
Component: the compliance-check subgraph (`subgraphs/compliance_check.py` — `run_generic_compliance_verdict`,
new `sentence_facts_fn`). Raised by: RuleWright (product), engine issue 0010.
Related: ADR-0060 (the `sources` seam, same shape), ADR-0061 (subject-document per-section facts + the
finding-count characteristic this shares), ADR-0057 (async engine).

## Context

Engine issue 0010. `run_generic_compliance_verdict` judged the **whole subject as one `CheckableFact`**
(`generic_facts_fn`), and `assemble_finding` cites the input faithfully — so **every finding quoted the entire
document**, identical across findings. Three sentences about three different rules were each cited with all
three; at a 20-page scale the UI's "IN YOUR DOCUMENT" quote becomes "your problem is somewhere in here."

The finer path already existed — `production_generic_compliance_check` accepts a `facts_fn` (added with the 0008
document path) — but the text entrypoint did not forward one. The capability existed; only the seam was missing.
This is not a defect; it is the documented refinement `generic_facts_fn`'s own docstring named.

## Decision

1. `run_generic_compliance_verdict` gains **`facts_fn: Any = None`**, forwarded to the graph builder (same shape
   as ADR-0060's `sources`).
2. Add **`sentence_facts_fn(subject_text, source_doc)`** — one `CheckableFact` per sentence, split via
   `segment_clause` (sentence terminators, abbreviation- and decimal-safe, so "Dr. Miller" and "$99" do not
   split), falling back to the whole subject if segmentation yields nothing.
3. **Sentence segmentation is the DEFAULT** (`facts_fn or sentence_facts_fn`), so each finding cites the sentence
   it is about out of the box; `facts_fn=generic_facts_fn` restores the old whole-subject behavior.

## The default change — decided by the product owner, against the engineer's recommendation

The engineer recommended **keeping `generic_facts_fn` as the default** (back-compatible; opt in to sentence
granularity), matching how ADR-0060 added `sources`. **The product owner explicitly overrode this and chose
sentence segmentation as the default** for better citation precision out of the box. Recorded here per that
instruction. The old behavior remains one argument away (`facts_fn=generic_facts_fn`).

## Consequences

- **Each finding cites its own sentence** — the report is precise on both sides (the rule side already was), and
  the human-review loop (PR-25/PR-27) can go straight to the wording to change.
- **Cost — the cross-product characteristic (same as ADR-0061), now on by default.** In the generic path the
  graph's `claims_fn` IS the `facts_fn` (deterministic, no model — `extract_claims` does not distill), so **claims
  == facts == sentences, 1:1**: a 200-sentence document yields 200 claims, not fewer. Findings are then
  per-sentence × selected-requirement, where selected-requirement ≈ **min(k, #requirements-in-scope) + context**
  (top-k semantic narrowing in `build_select_fn`, k=8 for the generic path, capped by how many requirements the
  scope actually holds). So a 3-sentence ad against a 3-rule policy ≈ 3 × 3 = 9 judge calls; a ~200-sentence
  document against ≥8 relevant rules ≈ 200 × 8 ≈ 1600, vs. ~8 for the old whole-subject default — i.e. ~Nx more
  judge calls at document scale (N = sentence count). This is the deliberate trade of the default change.
  **Follow-ups (not done):** cap the fact count, fall back to paragraph-level segmentation for large subjects, or
  lower `k` if the cost bites — the `facts_fn` seam makes any of these a drop-in.
- **The principled endpoint — an LLM claim-extractor `facts_fn`.** The granularity dial has a third setting beyond
  whole-subject and per-sentence: a producer that reads the subject and emits the ~N *distinct assertions* (so a
  200-sentence document might yield 20 claims, not 200), cutting judge calls and citing the actual claim. It is
  NOT the default because it costs (a) determinism (an LLM decides what a claim is, +1 call up front) and (b) the
  verbatim-citation guarantee (`assemble_finding` currently cites the input faithfully; a distilled claim is
  LLM-authored text, not a verbatim span). The `facts_fn` seam admits it with no graph change if a caller wants
  that trade — recorded here as the intended future option, not built.
- **Back-compatible via the seam:** `facts_fn=generic_facts_fn` is the exact prior behavior; the uploaded-document
  path (`run_compliance_document_verdict`, ADR-0061) is unchanged (it passes its own per-section producer).
- New public surface: `run_generic_compliance_verdict`'s `facts_fn` parameter and `sentence_facts_fn`.
