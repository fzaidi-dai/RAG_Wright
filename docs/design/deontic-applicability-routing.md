# Engine design: deontic + actor applicability routing (engine issue 0012)

**Raised by:** RuleWright (product), engine issue 0012 · **Date:** 2026-08-31 · **Component:**
`subgraphs/compliance_check` (`rule_scope_of`, `applicable_claim_types`, `build_select_fn`, the retrieve/judge
graph nodes) + `capabilities/assertion_extraction` (SEG subject-side extraction) · **Type:** an ontology lever
wired to one corpus (a hardcode to remove before beta) · **Relates to:** ADR-0040 (neuro-symbolic judge),
0007 (named-policy scoping), the SEG arc (verbatim per-chunk extraction), ADR-0044 (exception linking).

## The core axiom this serves (do not lose it)

**Neuro-symbolic:** the KG (symbolic layer) grounds, CONSTRAINS, gates, and feeds well-structured context; the
LLM REASONS over that bounded, grounded context — never brute-force. This change is a **gap-fill on that axiom**:
today the KG's typed rule fields (`deontic_type`, `actor`, `applicability_scope`) are inert, so the LLM
brute-forces every `assertion × rule` pair. The fix makes those fields **load-bearing symbolic gates** and
reserves the LLM for one grounded reasoning call over retrieved evidence.

## The problem (grounded)

The compliance selector has two narrowing levers, and both look up **FTC 16 CFR 255 section numbers**:
`SECTION_RULE_SCOPE` (`rule_scope_of`, unknown → `CONTENT`) and `SECTION_CLAIM_TYPES` (`applicable_claim_types`,
unknown → all claim types). A customer policy (`§ 1/§ 2/§ 3`) matches neither, so:

- **every rule becomes `CONTENT`** — the always-include (`CONTEXT`) guarantee for disclosure-style rules never
  fires (silent recall loss at scale);
- **`applies_to` returns true for everything** — no claim-type narrowing;
- selection degenerates to semantic-only top-k, so pairs = `assertions × min(k,#rules)`, **each an LLM judge
  call**; a 100-assertion doc is ~800–1,100 calls;
- **obligations are judged per-assertion**, and a per-sentence judge is *structurally incapable* of answering
  "is the connection disclosed *anywhere*?" → the measured 3× "unclear" on one disclosure rule.

`deontic_type` and `actor` are extracted, validated, and persisted on `Requirement` — and used nowhere in
routing. **The ontology knows what these rules are; the router does not ask.**

## Settled decisions (product owner)

1. **Do it now, both phases, no postponing** (an MVP that hardcodes/fails on the common customer-policy case is a
   beta failure, not an acceptable shortcut). Design the full scalable solution up front; sequence the build in
   gated phases for reviewability.
2. **All FOUR deontic cases handled**, each with its own breach semantics and judge unit — not a binary:

   | Deontic | Breach = | Judge unit | Route |
   |---|---|---|---|
   | **obligation** | absence of a required thing | **document-scoped, ONCE** (actor-gated, over retrieved evidence) | CONTEXT-path |
   | **prohibition** | presence of a forbidden thing | **per-assertion**, where the subject asserts something related | CONTENT-path |
   | **permission** | (cannot be violated standalone) | **not judged for violation** — an exception/safe-harbor that MODIFIES an O/F rule | excluded / exception-link |
   | **ambiguous / missing** | unknown | **recall-first: per-assertion + flagged** (never silently dropped) | CONTENT-path + flag |

3. **"Document-scoped" ≠ whole document in one prompt.** An obligation is asked ONCE *for the document* (the
   unit of the question), over a **bounded, retrieved** context: symbolic actor gate (KG, zero LLM) → vector
   retrieval of the few relevant passages (zero LLM) → **one** LLM reasoning call. Cost = obligations-passing-
   the-gate × 1, over small contexts — not `assertions × rules`.
4. **Subject-side scope via the dimension-agnostic `Constraint` producer**, NOT a hardcoded `actor` field: SEG's
   per-chunk extractor emits `actor` (and any `Constraint`s it can infer); the existing `constraint_scope_fn` +
   `constraint_applies` + `Requirement.applicability_scope` machinery (COMP-APPLIC-1, already built, no producer)
   does the matching.
5. **FTC tables become curated OVERRIDES**, deontic type is the default — a hand-curated domain pack can still
   pin scope, but nothing depends on the policy being FTC.

## The design

Two symbolic levers + a judge-node redesign, all KG-driven; the LLM reasons last, over bounded retrieved context.

**Lever 1 — deontic routing (rule side).** `rule_scope_of` (and a new `deontic_route`) derives the judge path
from `deontic_type` (per the table), with `SECTION_RULE_SCOPE` demoted to an override. `applicable_claim_types`
(ad path) likewise routes on `applicability_scope` constraints, FTC table demoted to override.

**Lever 2 — subject scope producer (Constraint).** SEG per-chunk extraction emits `actor` + inferable
`Constraint`s per assertion; aggregate to the document's actor-set / constraint-set. Feeds `constraint_scope_fn`
(prohibition narrowing) and the obligation actor-gate.

**Judge-node redesign** (`retrieve_applicable` + `judge` split by deontic route):
- **Prohibition path (per-assertion):** `assertion × selected prohibitions` (narrowed by `constraint_scope_fn` +
  semantic), per-pair judge — the existing shape, but only for prohibitions and properly narrowed.
- **Obligation path (document-scoped, once):** for each obligation → **actor gate** (actor absent in the
  document ⇒ skip, zero LLM) → **retrieve** the bounded relevant passages (actor-mentioning + semantically near
  the obligation) → **one** judge call `(obligation, evidence-bundle)` → one finding (disclosed / absent →
  violation), cited to the retrieved passages.
- **Permission path:** excluded from violation-judging; where it is a conditional exception, linked as a defense
  to the related O/F rule and passed as structured context to that judge (ADR-0044 pattern, requirement side).
- **Ambiguous path:** recall-first per-assertion (like prohibition), flagged so a mislabeled rule is never
  silently dropped.
- **Assemble:** merge findings from all paths; the gap matrix rolls up per requirement (unchanged shape).

## Grounding confirmed (2026-08-31)

- `DeonticType` = exactly `obligation` / `prohibition` / `permission` (+ extractor `AMBIGUOUS` coercion for
  off-vocab); `trigger_condition` is an orthogonal conditional field.
- `Requirement` carries `deontic_type`, `actor`, `applicability_scope: list[Constraint]`, `trigger_condition`,
  `evidence_standard`, `severity` — fully typed rule side. `CheckableFact` is bare (id + text + provenance +
  SEG's section/element); `Claim` (ad) already carries `actor`/`claim_type` — proof the shape works.
- `build_select_fn` already has the `constraint_scope_fn` seam (dimension-agnostic, `constraint_applies` matches
  `applicability_scope`) with NO producer on the generic path.
- The graph is `extract_claims → retrieve_applicable (pairs = claims × select_fn) → judge (ajudge_pairs, per-pair,
  concurrent) → assemble`. `ajudge_pairs` is already concurrent (gather + Semaphore) — the issue is call COUNT.
- The requirement-extraction template already extracts `deontic_type`/`actor`/`claim_types` — the SEG subject
  extractor mirrors that shape (Lever 2).
- Exception machinery exists on the clause side (`clause_exception_linking`, `IsExceptionTo`, ADR-0044) — the
  pattern to reuse for permission-as-defense.

## Task breakdown — Phase 1 (deontic routing + obligation-once), then Phase 2 (symbolic gates). Both now.

Each task: TDD (contract → failing test → implement) + a LIVE gate.

### Phase 1 — the cost + "unclear" fix (no new subject extraction; retrieval is semantic)
- **DEON-1 (deontic routing, rule side):** `deontic_route(requirement)` derives the path from `deontic_type`
  (obligation/prohibition/permission/ambiguous per the table); `rule_scope_of` uses it; `SECTION_RULE_SCOPE`
  demoted to override. Live: a customer `§1/§2/§3` policy routes correctly by deontic type.
- **DEON-2 (judge split):** split `retrieve_applicable`/`judge` into the prohibition path (per-assertion) and the
  obligation path (document-scoped); permissions excluded; ambiguous recall-first. Assemble merges.
- **DEON-3 (obligation-once, retrieved & bounded):** for each obligation, embed it, retrieve the top-N relevant
  subject assertions, judge ONCE over that bounded bundle (absent ⇒ violation). No per-sentence obligation calls.
- **DEON-4 (Phase 1 live gate):** the 0012 repro (3-rule policy, 3-sentence doc): the `§ 2` disclosure obligation
  is judged ONCE (not 3×) and answers definitively (not "unclear"); total judge calls drop materially.

### Phase 2 — the symbolic gates (zero-LLM narrowing; the scale fix)
- **DEON-5 (subject scope producer, ask-first contract):** SEG per-chunk extraction emits `actor` + inferable
  `Constraint`s; aggregate to the document scope. (Contract: how the assertion/document carries scope — a
  subject-scope structure vs fields on `CheckableFact`; ask-first.)
- **DEON-6 (constraint routing, generic path):** wire `constraint_scope_fn` in `production_generic_compliance_check`
  so prohibitions narrow by `constraint_applies(requirement.applicability_scope, subject_scope)` — dimension-
  agnostic, any domain.
- **DEON-7 (obligation actor-gate):** an obligation whose `actor` is absent from the document scope ⇒ skipped,
  ZERO LLM calls; present ⇒ retrieval scoped to the actor's passages.
- **DEON-8 (ad-path parity):** `applicable_claim_types` routes on `applicability_scope` (dimension `claim_type`),
  FTC `SECTION_CLAIM_TYPES` demoted to override — same root cause, removed here too (not left as a hardcode).
- **DEON-9 (permission-as-defense):** a conditional permission/exception is linked to its O/F rule and passed as
  structured context to that judge (ADR-0044 pattern), so a carve-out does not produce a false violation.
- **DEON-10 (Phase 2 live gate):** a customer policy + a document where an obligation's actor is absent → zero
  calls for it; cost scales with RELEVANT pairs, not `assertions × k`; a carve-out is honored.
- **DEON-ADR:** ADR extending ADR-0040 — deontic type + actor as the primary applicability gates; FTC tables as
  curated overrides; the obligation-vs-prohibition judge-unit split.

## Open decisions to confirm at review

1. **Subject scope carriage (DEON-5):** a document-level `SubjectScope` (actor-set + constraint-set) vs optional
   `actor`/`scope` on `CheckableFact`. Recommendation: aggregate document-level scope + keep per-assertion actor
   for obligation passage-scoping. Ask-first (contract).
2. **Obligation retrieval budget (DEON-3/7):** top-N passages + a token cap; over-budget ⇒ bounded map-reduce
   screen (chunk-level candidate screen, escalate only candidates). Pick N + cap empirically.
3. **Ambiguous-deontic default:** per-assertion + flag (recall-first) — confirm this over "judge both ways".
4. **Permission-as-defense (DEON-9):** exclude-only (Phase 1) is the floor; full exception-linking is Phase 2 —
   confirm it belongs in this arc (not postponed), given carve-outs are a common policy shape.
