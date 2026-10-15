# RuleWright: moving to rag-wright 0.11

What 0.11.0 (released 2026-10-15: PyPI and GitHub release `0.11.0`) changes for RuleWright. Do the 0.10 guide
first. Nothing here is edited from the engine repo, and nothing in 0.11.0 breaks an existing call.

## 1. The product-building skills ship in the wheel (PS-40)

`building-a-product-backend`, `building-a-product-frontend` and `specifying-a-product-on-the-engine` are engine
skills now, with the 0.10.0 corrections. Retire `generic-product-dev-skills/` and `tests/test_product_dev_skills.py`
(answered in our note of 2026-10-15); link any you want from the installed engine (ADR-0134).

## 2. Register only the engine capabilities you use (PS-42, optional)

The docs now register engine capabilities by slug rather than all of them:
`for m in engine_capabilities(): if m.slug in USED: register_capability(m)`, raising when a slug in `USED` is no
longer offered. Registering every one also puts the ones you never call into `discover()`'s ranking pool.
`packs/contracts/pack.py` (`(*engine_capabilities(), *CONTRACT_SPECS)`) and `register_engine_capabilities()` in
`engine/seam.py` register all of them; that still works. Change it if your agent plans over `discover()`.

## Verify

Your fast and db suites green on `>=0.11.0`; bump the floor. No harness name changed.
