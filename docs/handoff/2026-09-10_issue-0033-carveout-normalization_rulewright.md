# RuleWright handoff: engine issue 0033 resolved — `carve_out` now extracted from the contract, not just the query

Date: 2026-09-10 · **Re:** engine-issue 0033 · on `origin/main` (commit `4093c3c`) · ADR-0096 · **No API change. Re-ingest needed to populate it on existing KGs. Your `xfail(strict=True)` will XPASS — remove the marker.**

---

## TL;DR

Your diagnosis was right — it was a document-side gap, not a missing capability — but the root cause was one level down from the prompt. The model **was** extracting the carve-out; it emitted it as the **verbatim clause phrase** (`"Except in respect of the Supplier's indemnification obligations under clause 8"`), and the enum normalizer only did an **exact** match, so it fell to `OTHER` and was dropped. The query side worked only because a short query ("...excludes indemnification from the cap") yields the clean token `indemnification`.

Fix: the normalizer now has a **keyword-substring fallback** on the three lexically-anchored list dimensions — `carve_out` (excepts), `covered_subject` (covers), `damage_type` (prohibits_damage). A value the model quoted verbatim now maps on the canonical token it contains.

**Live-verified on your exact reproduction contract:** the `Cap On Liability` clause now extracts `carve_out = indemnification` (grounded `EXTRACTED`), and every other property you listed (`cap_basis`, `cap_quantum`, `temporal_bound`, …) still extracts — no regression. "liability" / "total aggregate liability" correctly stay unmapped (no false positive).

## Where to read it

Exactly where you expected: `carve_out` is now a clause property on the `Cap On Liability` clause, same as the other seven. No new node type, no `covered_subject` detour, no `Exception` node to chase — it comes back in the clause's property list. (The `ExceptionModel` you saw named in a query-extraction warning is the same enum behind `carve_out`; it was the *normalization* of that enum that was dropping the value.)

## What you must do

- **Re-ingest** the contracts you need this on. The value is produced at ingest, and no store-side backfill can recover a value the old ingest dropped. Fresh ingests get it automatically.
- **Remove the `xfail(strict=True)` marker** on "uncapped indemnity" — after re-ingest that condition fires its distinguishing `carve_out` property, so the strict-xfail would XPASS and fail your run.

## Why it's safe (not just a looser matcher)

The keyword fallback is deliberately narrow:
- **Opt-in, three dims only.** Enabled solely on the value-bearing LIST dims (`carve_out`/`covered_subject`/`damage_type`). Scalar dimensions are untouched — exact-match only, no behavior change.
- **Grounding-gated.** All three are lexically-anchored (ADR-0028), so a spurious keyword hit whose cue is absent from the clause text is downgraded to `AMBIGUOUS`, not asserted.
- **Distinctive tokens, longest wins.** Only value tokens ≥ 5 chars match, and the longest match wins — so "indemnification"/"gross negligence"/"confidentiality" match their phrases, and short/ambiguous fragments don't.

## On model choice

You may recall the standing note to try qwen-3.8 if granite is below par. Not needed here — **granite produced the carve-out correctly** once the normalization was fixed; the model was never the weak link on this. The fix is deterministic (a normalization change), no prompt or model swap.

Reference: ADR-0096, `ontology/clause_template.py` (`_normalize_enum` keyword fallback + the `excepts`/`covers`/`prohibits_damage` validators), `spans/clause_kg_extractor.py` (`_LIST_ENUM_DIMS` mapping, unchanged).
