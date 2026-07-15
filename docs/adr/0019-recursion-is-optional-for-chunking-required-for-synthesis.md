# ADR-0019: Recursion is available-when-warranted for chunking, intrinsic to synthesis

Date: 2026-07-15. Status: Accepted. Records why the RLM chunking capability (T17) does **not** gate on the
ADR-0016 fail-if-absent recursion test, while RLM synthesis (T28) and the RLM method (T15) do. Answers
"why doesn't chunking prove recursion the way synthesis must" once, so a future reader does not re-derive
it or contrive a recursion test onto chunking.

## Context

ADR-0016 defines RLM by four required capabilities, one of which is **recursive input decomposition**, and
enforces it with a fail-if-absent test. That test is correct for the RLM *method* and for *synthesis*,
where the working set (a large candidate set, an intermediate synthesis) is genuinely unbounded and must
be decomposed to arbitrary depth. Forcing the same gate onto chunking would be the flatten-and-hardcode
trap inverted: contriving an input just to make recursion fire, testing the mechanism instead of the
capability's real value.

Chunking's value is **LLM-found semantic boundaries** (T17, no fixed-size chunking ever). A document is
explored and partitioned into coherent spans. Whether that exploration recurses is **data-dependent and
usually unnecessary**: most documents' structure is judged in one exploration pass. Recursion is
**available when warranted** — a section too large to judge in one pass may be decomposed further via the
same machinery (`build_rlm_agent`'s sub-agents) — but it is not the property that makes a chunk good. A
boundary is good because it keeps a coherent unit whole and does not cut at a fixed size, not because it
was reached recursively.

## Decision

- **Chunking does not gate on recursion.** T17 carries no fail-if-absent recursion test. Its fail-if-absent
  discipline instead proves the properties that *are* chunking's value and failure modes:
  1. boundaries are **LLM-found and semantic, not fixed-size** (spans over the document's structural
     items; a live test asserts a known coherent clause is not split across chunks — boundary quality,
     the T15-opaque-proof analogue);
  2. **per-slice tool use** works where the exploration uses it (the `peek` tool during exploration);
  3. the **id/gate/validation** layer is correct over the discoverer's spans, including the T-CHK floor
     and near-empty rejection (an LLM can just as easily emit a boundary around a lone heading).
- **Recursion stays available, not forbidden.** The chunking discoverer uses the full RLM machinery, so a
  genuinely over-large section can be decomposed by a dispatched sub-agent. This is opportunistic, not
  required, and is not asserted.
- **Synthesis (T28) and the method (T15) keep the recursion gate.** There, recursive decomposition to
  arbitrary depth is the paradigm and is proven with the ADR-0016 fail-if-absent recursion test (T15's
  hermetic depth assertion with its flat-workflow teeth).

## Consequences

- The T17 test suite intentionally omits a recursion assertion; this ADR is the recorded reason, so its
  absence reads as a deliberate scoping decision, not an oversight.
- If a future corpus needs recursive chunking as a hard requirement (e.g., documents too large for a
  single exploration pass to be reliable), that is a new requirement with its own gate — not a silent
  reinterpretation of T17.
