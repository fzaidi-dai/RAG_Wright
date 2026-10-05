# RuleWright handoff: 0035 follow-up — the tenant guard now derives its server list (no hand-maintained enumeration)

Date: 2026-09-11 · on `origin/main` (commit `e3943e7`) · extends ADR-0099 · **Test-only change; behavior unchanged.**

---

You were right, and it's fixed. The gap was real: the guard enumerated the four servers as a hand-written dict, so a fifth server that forgot to register with the guard would be silently unchecked — the guard disarmed by the same omission it exists to catch.

## The fix

The guard now **discovers** servers instead of listing them:

- `pkgutil.iter_modules` over `rag_wright.mcp` finds every `*_server` module;
- for each, it finds the `build_*_mcp` builders and **asserts each server exposes at least one** (a server the guard can't check is itself a failure);
- it builds each with stubbed runners — a stub is injected for every non-config parameter, so **optional tools register too** (verified: compliance's `check_compliance` and `check_compliance_document` are covered, not just `check_ad_compliance`);
- it checks every tool's input schema for a forbidden tenant/database/scope parameter.

So a new `*_server.py` is covered the moment it exists. **Forgetting is now the failure, not the exemption** — exactly the property you asked for. The detector test (a deliberately-leaky server is flagged) stays, so both claims hold: "the check works" and "the check runs on everything."

This is the same shape as your suggested `pkgutil` discovery. (An ARD-driven enumeration would serve equally, but it would need every `mcp_tool` registration resolvable back to its builder, which isn't wired today; module discovery gets the derived-list property with no new coupling. If we later want the ARD cross-check — every registered `mcp_tool` has a discovered server and vice-versa — that's a small addition on top.)

## The other two conditions (confirming your verification)

- **Store cache dropped, deliberately.** Your measurement (rebuild ~1.3 ms, 0.09% of a ~1,400 ms call) confirms the per-request leg rebuild is negligible, so there is no store-identity cache — no cache means no cache-key bug class, which is what 0034 was. Agreed it's the simpler correct answer.
- **Env fallback fails closed.** `resolve_request_store` returns `env_store` only when no resolver is wired; a configured resolver's result is returned as-is and never backfilled from `QA_DB`, so a resolver that can't find tenancy raises and that exception is yours to own.

Nothing changes for your Phase 5a plan — point your agent at the servers directly with a `store_resolver`.

Reference: commit `e3943e7`, `tests/mcp/test_no_model_supplied_tenant.py` (`_discover_servers`, `_build_with_stubs`), ADR-0099.
