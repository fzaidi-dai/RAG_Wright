# ADR-0044 (DRAFT / PROPOSED): The IS_EXCEPTION_TO derived relationship (cap ↔ uncapped carve-out)

Date: 2026-08-07
Status: **Accepted**

## Settled decisions (review approved 2026-08-07)

1. Refinement signal: **proximity-only** first (deterministic, no LLM); add an LLM reading-check only if measured insufficient.
2. Confidence: **link (INFERRED) when proximity-supported; skip otherwise** (no weak AMBIGUOUS edge for distant co-occurrence).
3. Edge: **`IS_EXCEPTION_TO`** (exception/uncapped clause → cap clause); query traverses `in('IS_EXCEPTION_TO')` from the cap.
4. Scope: **general edge type, populate cap↔uncapped only** for now.
5. **Build then measure** on the same query before any generator-prompt tweak.

## Context

Phase-A validation found `intra_document_qa` (Leg A) abstains on *"How is liability capped in this contract, and
under what conditions?"*. The query-function classifier returns `[Cap On Liability, Uncapped Liability]`, and the
KG represents the contract's liability structure as **two disconnected fragments**:
- a `Cap On Liability` clause, usually **without** its carve-outs, and
- a separate, often property-less, fragmentary `Uncapped Liability` clause ("(b) any negligence or fault;"),

with **no encoded relationship** between them. The generator sees two contradictory-looking fragments and
abstains, instead of synthesizing "capped at X, **except** uncapped for negligence/fault."

Naively "dropping Uncapped as a false positive" is WRONG: the uncapped carve-outs ARE the "conditions" the query
asks about — dropping them loses half the answer.

KG measurements (ragwright_cuad_full, 506 contracts) show this is a systematic structure, not an edge case:
- **M1 (co-occurrence):** 373 contracts have a Cap clause; **182 (49%) also have an Uncapped clause.**
- **M2 (carve_out coverage):** only **80 / 2097 cap clauses (4%)** carry a `carve_out` (EXCEPTS) property; in
  **141 / 182 co-occur contracts (77%)** the cap clause has NO carve_out — the Uncapped clause is the *only*
  carve-out representation.

So the **extraction lever** (capture carve_outs) is impractical as a first move (96% gap over ~2000 clauses = a
large, uncertain re-ingest). The **symbolic lever** works on the *existing* KG: both clauses already exist for
the 182 co-occur contracts; they just aren't related.

## Decision (proposed)

Add a **DERIVED, INFERRED relationship** `IS_EXCEPTION_TO` (Uncapped clause → the co-occurring Cap clause),
computed over the existing KG in a post-ingest pass — like `party_clause_linking` (KG-7), **no re-ingest**.

**Confidence = INFERRED (FR-S.4).** It is a reasoned inference (co-occurrence + refinement), never an extracted
fact. It flows into query-time evidence so the generator weights it as inferred and cites the uncapped clause's
span; a reviewer can validate it (never a silent hard claim).

**Computation (a new derived-relationship capability, e.g. `clause_exception_linking`):**
1. **Base signal (symbolic):** within a contract, each `Uncapped Liability` clause co-occurring with a
   `Cap On Liability` clause is a candidate exception to that cap.
2. **Refinement (to avoid false links):** positional/section proximity — the uncapped clause's document offsets
   near/within the cap clause's neighborhood (both clauses have doc_start/doc_end via their spans). Optionally a
   light LLM check ("does this uncapped clause read as an exception to that cap?"). Proximity-matched → INFERRED;
   co-occur-only-but-distant → AMBIGUOUS or dropped.
3. **Write** the `IS_EXCEPTION_TO` edge (Uncapped → Cap) with confidence INFERRED and provenance (the source
   contract / spans), additively — touches no existing clause, property, or identifier.

**Query consumption:** when `intra_document_qa` serves a `Cap On Liability` clause, it also pulls the cap's
`IS_EXCEPTION_TO`-linked uncapped clauses as its carve-outs/conditions. The generator then answers "capped at X,
**except** uncapped for [carve-outs]" from *structured* evidence, with the exceptions tagged INFERRED and cited.
This also resolves the classifier over-return: the Uncapped clause is no longer contradictory noise — it is
structurally the cap's exception.

## Consequences

- **No re-ingest.** A derived-relationship pass over the existing KG (the `party_clause_linking` pattern), run
  once, idempotent; re-runnable on both the local and Modal KGs in place.
- **Schema change (ask-first):** a new `IS_EXCEPTION_TO` edge type. Additive; the standing rule "changing the
  data model/schema is ask-first" applies — this ADR IS that ask.
- **Honest confidence:** the relationship is INFERRED, surfaced at query time and human-validatable — no claim
  without a citation, no inference asserted as fact (FR-S.4 / FR-Q.6).
- **Neuro-symbolic, ADR-0040-aligned:** symbolic co-occurrence + a proximity/reading refinement → an INFERRED KG
  relationship the generator reasons over. The KG becomes the ground truth for the cap↔carve-out *structure*,
  not just the isolated facts.
- **Generalizable:** the "clause A is an exception/carve-out to clause B" pattern can extend to other function
  pairs later (e.g. a warranty disclaimer as an exception to a warranty) — start scoped to cap↔uncapped.

## Open questions for review (settle before implementing)

1. **Refinement signal:** start **proximity-only** (pure symbolic, deterministic, cheap) and add the LLM
   reading-check later, or include the LLM check from the start? (Recommend proximity-only first.)
2. **Confidence gradation:** INFERRED for proximity-matched links; AMBIGUOUS (or drop) for co-occur-only-but-distant?
3. **Edge direction/name:** `IS_EXCEPTION_TO` (uncapped → cap) — or `HAS_CARVE_OUT` (cap → uncapped)? (Query
   consumption reads from the cap, so either works; pick the more natural traversal.)
4. **Scope now:** cap↔uncapped only, or design the edge as a general `IS_EXCEPTION_TO` from the start (same edge,
   future function pairs)?
5. **Where it runs:** a new registered `function`/derived-linking capability (deterministic if proximity-only),
   run as a post-ingest pass; the query side (`intra_document_qa`) consumes the edge.
6. **Does it fully fix the abstain,** or is a generator-prompt tweak still needed to synthesize "capped except X"?
   (We can measure after building the relationship, on the same query.)
