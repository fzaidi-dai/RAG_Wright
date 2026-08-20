# ADR-0060: Scope a compliance check to named policy sources (a database-side `sources` filter)

Status: Accepted (2026-08-20)
Date: 2026-08-20
Component: the compliance-check subgraph (`subgraphs/compliance_check.py` — `run_generic_compliance_verdict`,
`production_generic_compliance_check`, `run_compliance_check`, `production_compliance_check`), the Requirement
store seam (`store/arcadedb.py` — `all_requirements`, new `requirement_sources`), and the compliance MCP tools
(`mcp/compliance_server.py` — `check_compliance`, `check_ad_compliance`). Raised by: RuleWright (product),
engine issue 0007.
Related: ADR-0050 (PROD-3 lossless compliance ingest — the Requirement KG), ADR-0047 (whole-index retrieval —
narrowing is a ranking step, not a gate), ADR-0052 (engine/product split — the product hand-builds
orchestration and consumes these entrypoints), ADR-0057 (async engine — the entrypoints are async).

## Context

Engine issue 0007, a capability gap (not a defect). The engine's compliance design assumed **one regulatory
corpus per database**: curate FTC 16 CFR 255 into `ragwright_compliance`, check a subject against that corpus.
Under that assumption "check against the whole store" *is* "check against the policy," because there is one
policy. `run_generic_compliance_verdict` → `production_generic_compliance_check` loads
`store.all_requirements()` — every requirement row, every policy — and there is no way to narrow it.

RuleWright's PR-41 is a different shape: **bring-your-own-policy, per check.** A user uploads one subject
document and one policy and expects the answer to be about that pair. In a shared database that assumption
breaks — supplying policy bytes controls what is *added* to the store, never what the subject is checked
*against*. Verified: a compliance DB holding 6 requirements across 3 policies checks a fresh subject against all
6. The endpoint promised "check this document against this policy" and delivered "check against everything
curated here, having just added yours." The product could compose the filter itself from the public
`build_select_fn`/`build_compliance_check`, but that forks wiring the engine owns; the right fix is engine-side.

Requirements are extracted at ingest time from each policy: one policy document yields many small structured
requirement rows (one per rule), each tagged with its policy `source`. So the store already carries the exact
key a scope filter needs — `source` — and already uses it elsewhere (`ingested_citations(source)`).

## Decision

Add an optional `sources: list[str] | None` filter to the compliance verdict path. `None` preserves today's
store-wide behaviour exactly (back-compatible by construction); a list scopes the check to those named policies.

**The filter is pushed into the DATABASE, not applied in memory.** `all_requirements(sources=...)` narrows with
`WHERE source IN [...]`, so a store holding thousands of requirement rows across many policies or tenants never
fetches (or embeds, in the downstream semantic narrowing) the rows outside the requested scope. A load-all-then-
filter-in-Python version would have been simpler for today's small compliance DB, but it does not hold at scale
and this project designs for scale (cf. ADR-0057, converting the whole engine to async rather than patching the
deadline case-by-case). An empty scope (`sources=[]`) returns no rows without issuing a query.

**An unknown source name is an explicit error, not a silent empty match.** A new `requirement_sources()` store
method returns the DISTINCT set of policy `source`s present *without loading any rows*; `_load_requirements`
validates the requested names against it and raises `UnknownComplianceSourceError` (carrying `.unknown` and
`.present`) on any that do not exist. "You named a policy that does not exist" and "zero requirements consulted"
are different problems for a user — the product already maps a zero-requirement check to `not_checked`, so an
unknown name must be distinguishable from an empty-but-valid scope.

- `None` → the whole store (unchanged). `requirement_sources()` is NOT consulted in this path, so a caller with
  a minimal store still works.
- `[names]` → validate, then load only those policies' rows.
- `[]` → scope to nothing → zero requirements → the caller's `not_checked` semantics. Not an error (there are no
  *unknown* names in an empty list).

**Every surface that exposes the capability gets it, in the same change.** The parameter is threaded through the
generic path (`run_generic_compliance_verdict` / `production_generic_compliance_check`, the path PR-41 uses), the
advertising path (`run_compliance_check` / `production_compliance_check`, for symmetry), AND the MCP tools
(`check_compliance`, `check_ad_compliance` — FastMCP exposes `sources` on each tool's input schema; `CheckFn`
became a Protocol carrying the kwarg). Leaving the MCP tool store-wide after adding `sources` to the functions
would have been a half-fix — the same class of inconsistency as an internal helper diverging between two
callers — so it is explicitly NOT a non-goal.

## Consequences

- **Back-compatible.** Every existing caller passes no `sources` (or `None`) and behaves exactly as before; the
  two `all_requirements()` call sites are unchanged.
- **PR-41 can mean what it says:** a bring-your-own-policy check scopes to the supplied/named policy, and the
  report's `gap_matrix`/coverage (computed over the requirement pool, now pre-filtered) reflects only that
  policy automatically — no report-shape change.
- **Naming a curated standard by id** (not re-supplying its bytes every time) is now expressible — the reuse
  flow AC-41 implies ("retained and usable as a curated standard afterwards").
- **Scale-ready and tenancy-ready.** The same DB-side `source` filter is the mechanism Phase-2 workspace/tenancy
  scoping needs ("the policies belonging to this workspace"), so isolation does not have to lean on
  database-per-workspace as the only lever. Scoping also cuts the semantic-narrowing embed cost to the scoped
  rows.
- **A new store method** (`requirement_sources()`) and a new public exception (`UnknownComplianceSourceError`)
  are now part of the engine's compliance surface.
- **Not changed:** the MCP tools' report shape; the one-corpus-per-database deployment remains valid and is now
  just the `sources=None` case of a more general capability.
