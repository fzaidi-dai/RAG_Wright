# RuleWright handoff: engine rename — `run_compliance_check` → `run_ad_compliance_check`

Date: 2026-09-09 · on `origin/main` (commit `a70c931`) · **No API change for you. Runtime-safe. Two stale comment references on your side to refresh.**

---

## What changed

The subgraph entrypoint behind the `check_ad_compliance` MCP tool was named `run_compliance_check` — which reads as the *generic* top-level runner even though it is the **advertising-tuned** path (ad claim extraction + FTC 16 CFR 255 `claim_type` applicability). It's renamed to match the tool and to sit clearly beside its generic counterpart:

| Layer | Name |
|---|---|
| MCP tool | `check_ad_compliance` (unchanged) |
| Subgraph entrypoint | **`run_ad_compliance_check`** (was `run_compliance_check`) |
| Generic counterpart | `run_generic_compliance_verdict` (unchanged) |

## Impact on you: none at runtime

You call the **MCP tool** `check_ad_compliance` and the **generic verdict** path — neither imports or calls the renamed subgraph function. So nothing in your runtime breaks. No back-compat alias was added because nothing calls the old name.

## One tidy-up: two stale comment references

You mention the old name in **comments only** (not code), which now point at a renamed symbol:

- `src/rulewright/workflows/ingest_policy.py:24` — "...its own entrypoint `run_compliance_check`..."
- `src/rulewright/engine/seam.py:776` — "The GENERIC verdict, not `run_compliance_check`: the latter is advertising-tuned..."

Update those to `run_ad_compliance_check` when convenient. Purely cosmetic — no behavior depends on it.

Reference: commit `a70c931`, `subgraphs/compliance_check.py::run_ad_compliance_check`, `mcp/compliance_server.py`.
