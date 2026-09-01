# Handoff to RuleWright — the ontology-source-of-truth engine program (ADR-0066 + ADR-0067)

Date: 2026-09-01. From: RAG_Wright engine. Scope: what changed in the engine you consume, and the (small) set of
actions on your side. TL;DR: **the whole program is deliberately behavior-preserving for domain *knowledge*** —
vocabularies, enums, SHACL constraints, and the KG schema *shape/values* are unchanged (verified `loaded == old`
at every phase). Bump the engine version and the only real actions are the entity-resolution decoupling (§A) if
you resolve entities; §B is two ingestion *improvements* you'll simply benefit from.

The engine is now **ontology-driven end-to-end** (contract + compliance) and **domain-retargetable**: a new domain
is a `.ttl` pack, not an engine edit. Detail in `docs/adr/0066-*.md` and `docs/adr/0067-*.md`.

---

## A. Breaking — ONLY if you touch these (action required)

### A1. `Entity.cik` → `canonical_id`  (KG schema field rename, ADR-0067 P5c)
The SEC-specific `Entity.cik` column is renamed to a generic `canonical_id` (the resolver's canonical id — a CIK
for the SEC resolver, or your registry's id). `entity_id` / `node_key` (the vertex identity) are **unchanged**.

- [ ] **Migrate your populated *entity* KGs** so existing rows get `canonical_id`:
      `uv run python scripts/migrate_entity_cik_to_canonical_id.py <db1> <db2>`
      (loads `.env`, idempotent, **additive** — it keeps the old `cik` column for rollback; run it once per DB).
      We already ran it on the engine KG `ragwright_cuad_full` (1181 entities). Your `rulewright_dev` had 0
      entities (nothing to backfill; `ensure_schema` adds `canonical_id` on its next write); your
      `rulewright_compliance` has no `Entity` type (Requirement KG) — no action. Run it on any OTHER entity KG.
- [ ] **Update custom queries that read `Entity.cik`** → `canonical_id`. NOTE: the engine's own retrieval/graph
      methods use `entity_id`/`node_key`, so you're affected ONLY if you wrote your own `cik`-selecting SQL.

### A2. `EntityRegistry` API (ADR-0067 registry decoupling)
`EntityRegistry` is now domain-neutral (I own the engine AND the registry, so it no longer hardcodes CIK).

- [ ] `EntityRegistry.from_company_tickers(rows, ...)` **moved** → `corpus.edgar.build_edgar_registry(rows, ...)`
      (same signature). Update the import/call if you build the SEC/EDGAR registry.
- [ ] `EntityRegistry.skipped_ciks` **renamed** → `skipped_ids`. Update if you read it.
- [ ] `EntityRegistry(normalize=<fn>)` is a NEW **optional** kwarg (a surface-form normalizer; default is generic).
      Existing `EntityRegistry()` still works — no action unless you want a custom normalizer.

---

## B. Behavior enrichments — informational, no code change (but you'll notice)

### B1. Requirement applicability is now dimension-general (ADR-0066 P3a)
The requirement extractor gained an `applicability` field, and `to_requirements` maps generic `"dimension: value"`
conditions into `Requirement.applicability_scope` (recall-first). A customer policy's **non-`claim_type`**
conditions (jurisdiction, employee_class, data_category, …) now survive into the KG, and the DEON query gates on
them → **more precisely scoped compliance** for non-advertising policies. The `Requirement` contract shape is
unchanged (`applicability_scope: list[Constraint]`); it just carries richer content now.

### B2. The policy-ingestion section gate is now deontic-cue-based (ADR-0066 P3c)
`RegulationAdapter` / `DocumentRegulationAdapter`'s `skip_definitions` param (same name, same default `True`) now
skips a section when its text carries **no deontic cue** (must/shall/may/prohibited…, authored in the ontology),
replacing the old `"definition"`-in-heading keyword hack. Effect: it now **keeps** an operative rule sitting under
a "Definitions" heading and **drops** a cue-less "Scope/Purpose" section → fewer spurious requirements, heading-
agnostic. Which sections extract can differ from before; a truly modal-less declarative statement is treated as
non-operative (descriptive, not a testable rule).

---

## C. No action (transparent)

- **Vocab / enums / SHACL / KG schema**: same members, values, and shape (verified equal at every phase). Just
  bump the engine version.
- **The `.ttl` files ship with the engine** (`src/rag_wright/ontology/{contract_bridge,compliance_bridge}.ttl` +
  `packs/ftc_16cfr255.ttl`) — the loaders read them at runtime.
  - [ ] **One-line confirm on your build**: your installed engine includes `rag_wright/ontology/*.ttl` and
        `rag_wright/ontology/packs/*.ttl` (a standard hatch wheel includes them; verify if you vendor or trim).
- **Retargeting** (new for you to leverage, not required): domain knowledge now lives in the ontology. A new
  compliance/contract sub-domain is a `.ttl` pack (see `ontology/packs/ftc_16cfr255.ttl` as the reference), and
  entity resolution takes any registry (`EntityRegistry(normalize=...)`) — no engine edit.

---

## Quick action checklist

1. [ ] Bump the engine version.
2. [ ] Confirm `.ttl` files are in the install (§C).
3. [ ] If you resolve entities: run the `cik → canonical_id` migration on your entity KGs (§A1) + fix any custom
       `cik` queries + the two `EntityRegistry` renames (§A2).
4. [ ] Heads-up only: richer requirement applicability (§B1) + the cue-based section gate (§B2) — re-check any
       golden expectations tied to which sections extract.
