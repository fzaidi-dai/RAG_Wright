# Archive — superseded & historical material

This folder holds **superseded, delivered, or historical** documentation kept for the record. It is **not a source
of truth** and is **not shipped in the `rag-wright` package** (it lives at repo root, outside the wheel). Read it
only for history — how a decision was reached, what a since-delivered plan proposed, or a past run's numbers.

Nothing here reflects the current architecture by default: much of it predates the engine/product split and the
parking of GraphWright (ADR-0052). Where a document has been superseded, the superseding source is the current one
below.

## Where the current truth lives

- **Architecture / concepts:** `docs/architecture.md`, `docs/concepts.md`, `docs/contract_pipeline_explainer.md`,
  `docs/ARCHITECTURE_OVERVIEW.md`
- **Engine-platform boundary (post-0052):** `docs/specs/engine-platform/SPEC.md` + `TASKS.md`
- **This engagement (readying the engine for a new product):** `docs/specs/engine-prep/plan.md`
- **Building a new domain on the engine:** `docs/domain-adaptation/` (and `docs/product/new-domain-build-sequence.md`
  until it is folded in)
- **Decisions:** `docs/adr/` — see `docs/adr/README.md` for the grouped index (Current spine / Parked / Superseded)
- **Install / configure / quickstart / API reference:** `docs/installation.md`, `docs/configuration.md`,
  `docs/quickstart.md`, `docs/api/`

## Layout

- `plans/` — delivered build/design plans (their work has landed; see the ADRs they reference)
- `handoffs/` — cross-agent/session handoff logs (GraphWright-era and RuleWright-era issue logs)
- `results/` — historical benchmark/run reports
- `design/` — delivered engine-issue design notes
- `engine-issues/` — resolved numbered issue write-ups (each maps to an ADR)
- `misc/` — other superseded root/top-level docs (e.g. the legacy build plan and task ledger)

Files are moved here with `git mv`, so `git log --follow <path>` recovers their full history.
