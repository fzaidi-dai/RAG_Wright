# ADR-0067 (DRAFT / PROPOSED): Domain-pack retargeting — decouple the KG schema + entity resolution from contracts/CUAD/EDGAR

Date: 2026-09-01
Status: **Accepted** (design approved 2026-09-01; implementation phased, each phase gated)

The P5 program of ADR-0066 (deferred there as "a separate later program with its own ADR"). ADR-0066 made the
contract + compliance *knowledge* ttl-authoritative (vocab, template, SHACL constraints, requirement side, query
overrides — and P4b established the **domain-pack pattern**, `packs/ftc_16cfr255.ttl`). This ADR extends that pack
pattern to the **KG storage layer + entity resolution**, so a new customer domain is onboarded by supplying a
`.ttl` pack, not by editing engine Python.

## Context (the hardcoding, from the 2026-08-31 INGEST-NS review)

The shared ingestion front-end (parse → chunk → segment) and the retrieval index are already domain-neutral. But
the KG **storage layer** hardcodes the contract/CUAD/SEC domain, so retargeting requires editing engine source:

1. **The typed-edge map** — `store/arcadedb.py::_TYPED_DIMENSION_EDGE` maps each `PropertyDimension` to one of ~40
   **legal** edge types (`HAS_MUTUALITY`, `EXCEPTS`, `PROHIBITS`, `CAPS`, `GRANTS`, `SECURES`, `GOVERNED_BY`, …),
   plus `_edge_predicate_iri` (ODRL for deontic edges, the contract-bridge IRI otherwise). Contract-domain
   KNOWLEDGE, authored in Python. (Rule 1 of ADR-0066: this belongs in the ontology.)
2. **The node schema** — `ensure_schema` DDLs contract-specific vertex types (`Clause`, `Contract`,
   `PropertyValue`, `PartyTo`, `IsExceptionTo`) with contract-only fields (`agreement_type`, `parties_json`,
   `agreement_date`), alongside the genuinely GENERIC infra (`Chunk`, `Span`, `Entity` — the retrieval index + the
   graph node). A new domain cannot add its own node/edge types without editing `arcadedb.py`.
3. **Entity resolution** — `capabilities/entity_resolution.py` is closed-world to the **EDGAR CIK** registry;
   `Entity.cik` is a schema field, `entity_id` "is the CIK or an `UNLINKED:` surrogate" (`store/seam.py`). CIK is
   meaningful only for U.S. SEC filings; for any other domain the registry, the `cik` field, and the resolver are
   a dead corpus assumption sitting on the "generic" pipeline's resolve/write nodes.

## Decision — the three-layer line for the KG storage layer

Draw the same engine / domain-pack / corpus line the rest of ADR-0066 uses, now for storage:

| Layer | Owns | Where it lives |
|---|---|---|
| **Engine (generic)** | the retrieval index (`Chunk`/`Span`), the graph node (`Entity`), the write machinery, the store seam, the pack-reading DDL builder, the entity-resolution SEAM | code (domain-neutral) |
| **Domain pack (`.ttl`)** | the domain's node types + their properties, its typed edge-types + predicate IRIs, and the dimension→edge mappings | `packs/<domain>.ttl` (contract pack = the contract-bridge + a storage section) |
| **Corpus resolver (plug-in)** | the canonical-id registry + matching strategy (EDGAR-CIK is ONE implementation) | an injected `EntityResolver` |

Concretely:
1. **Typed-edge map → the ontology.** The contract pack declares, per dimension, its KG edge type + predicate IRI
   (the store loads them like P2 loaded the SHACL shapes — `_TYPED_DIMENSION_EDGE` / `_edge_predicate_iri`
   deleted). A generic-graph domain with no typed edges simply declares none.
2. **Node schema → the domain pack.** The pack declares its node types + fields; `ensure_schema` becomes a generic
   DDL builder that creates the ENGINE infra (`Chunk`/`Span`/`Entity`) + whatever the pack declares. The contract
   classes (`Clause`/`Contract`/`PropertyValue`/…) move into the contract pack's storage declarations.
3. **Entity resolution → a pluggable seam.** `EntityResolver` is an injected interface `(mention clusters) →
   resolved canonical ids`; the EDGAR-CIK resolver is one implementation (kept as the SEC pack's default). The
   generic default is the closed-world **surface-form registry** already present (no CIK): `entity_id` = the
   resolver's canonical id (a CIK for SEC, a surface-normalized id otherwise), never fabricated. The `cik` field
   becomes the resolver's concern (a generic `canonical_id` on `Entity`, or a resolver-supplied extra), so the
   engine schema carries no SEC assumption.

`entity_id` stays load-bearing (FR-S.2/S.3, ADR-0066 ask-first): the identifier SCHEME is unchanged (a canonical
registry id or an `UNLINKED:` surrogate); only its SOURCE becomes pluggable.

## Phased plan (each phase: TDD + a live A/B, like every ADR-0066 phase)

- **P5a — typed-edge map → the ontology.** The contract ttl declares each dimension's `cbr:kgEdge` + predicate
  IRI; `store/arcadedb.py` loads them (delete `_TYPED_DIMENSION_EDGE` / `_edge_predicate_iri`). Contained, mirrors
  P2. Live A/B: a real clause writes the SAME typed edges as before.
- **P5b — node schema from the pack.** The pack declares its node types + fields; a generic DDL builder in
  `ensure_schema` reads the pack + creates the engine infra; the contract classes move to the pack's storage
  declarations. Live A/B: `ensure_schema` produces an identical live schema.
- **P5c — the `EntityResolver` seam.** Extract the resolver interface; the EDGAR-CIK resolver becomes an injected
  implementation; the generic default is the surface-form registry; `Entity.cik` → a generic canonical-id.
  Import-linter/domain-neutrality check: the engine no longer names CIK/EDGAR. Live A/B: SEC-corpus resolution is
  unchanged with the CIK resolver injected; a non-SEC doc resolves via the surface-form default without a dead
  `cik`.

## Consequences

- **A new domain is a `.ttl` pack + (optionally) a resolver plug-in — not an engine edit.** The open-core promise,
  realized for the storage layer. The contract/CUAD/SEC specifics become the reference pack + the SEC resolver.
- **Consistent with the whole ADR-0066 arc** — same knowledge-in-the-ontology, mechanism-in-code line (Rule 1/3);
  the typed-edge map is knowledge (→ pack), the DDL builder + resolver seam are mechanism (→ code).
- **Large, phased, gated** — touches `store/arcadedb.py`, `capabilities/entity_resolution.py`, `store/seam.py`,
  the pipeline resolve/write wiring, and the contract ttl. Each phase is independently gated + a no-behavior-change
  live A/B (this is a refactor to move the source of the schema, not a behavior change).
- **`entity_id` scheme unchanged** (ask-first identifier held constant); only its source becomes pluggable.

## Settled decisions (approved 2026-09-01)

1. **Sequencing:** P5a (edge map → ontology) → P5b (node schema from the pack) → P5c (the `EntityResolver` seam),
   each independently gated, in that order.
2. **The contract pack's storage declarations extend `contract_bridge.ttl`** with a storage section (node types +
   `cbr:kgEdge` per dimension). A `packs/` split is deferred until multiple contract sub-packs emerge.
3. **`Entity.cik` → a generic `canonical_id`** on the engine `Entity` schema; the SEC resolver fills it with a CIK,
   another resolver fills it with its own id. One node-key concept, no SEC assumption in the engine schema.
4. **The generic default resolver is the existing exact-normalized surface-form registry** (closed-world, never
   fabricated); EDGAR-CIK is the SEC pack's injected override.
5. **A scope-guard import-linter rule** forbids the engine referencing `cik`/`edgar` once P5c lands (enforcing the
   decoupling, as for Product→Engine).
