# ADR-0013: Entity resolution matching strategy — exact normalized, closed-world

Date: 2026-07-13. Status: Accepted. Records the §16.3 matching-strategy decision for `entity_resolution`
(T24): exact normalized-surface-form match against the registry, closed-world, no fuzzy/embedding/LLM.

(Numbering note: the T24 ledger row referenced `docs/adr/0005-entity-resolution.md`, but 0005 is the
relational golden set — this ADR is 0013.)

## Context

FR-C.7 splits into `entity_disambiguation` (T23b) then `entity_resolution` (T24). T23b already collapses
surface-form variants of one entity into a canonical cluster (ADR-0004). T24 links each cluster to its
canonical `entity_id` — an EDGAR CIK — against the T8 registry, which indexes canonical names, tickers,
and former-name aliases by a normalized surface key and resolves closed-world (`None` for the unknown).
SPEC §16.3 left the concrete match open: exact, fuzzy, embedding, or LLM-assisted.

## Decision

**Exact normalized-surface-form match against the registry, closed-world.** A cluster resolves to the
first of its surface forms the registry knows; an unknown cluster resolves to `None` (unlinked), never a
fabricated id. **No fuzzy, embedding, or LLM matching.**

Rationale — the same conservative-merge bias as ADR-0004 (C3) and ADR-0012:
- A wrong fuzzy/embedding link is a **silent false merge onto a canonical entity** (e.g. linking "Acme
  Systems" to the CIK of the unrelated "Acme Corp"). That is the worst error class: it corrupts the graph
  invisibly, exactly what T26 will surface as fact and the reasoner will trust. An **unlinked** cluster is
  the safe failure — the human sees it and can link or reject it.
- T23b has already done the hard surface-form work, so exact-normalized is high-recall for entities the
  registry actually contains. Fuzzy matching mostly buys links to entities the registry does *not* contain
  — which should stay unlinked (private companies, T10), not be forced onto a near-neighbour CIK.
- Unlinked is a first-class, expected outcome: graph coverage is public-filer-centric (T10). A high
  unlinked rate is the corpus being honest, not a resolver failing.

Two post-resolution invariants live in this capability (they need resolved ids, so they cannot live in the
T4 contract): a relationship whose two refs resolve to the **same** `entity_id` is dropped as a self-loop;
and the standalone-mention and relationship-endpoint channels are resolved as one stream (a ref matches a
cluster key first, taking that cluster's id), so an entity appearing in both channels lands on one node.

## Consequences

- High precision, no false links; T24 links a clean cluster to a CIK rather than fighting variants.
- The unlinked rate will be high on this corpus and that is expected (private/variant entities absent from
  EDGAR); it is not evidence the resolver is weak.
- Fuzzy/embedding/LLM matching can be added later **behind the same registry `resolve` boundary, gated on
  evaluation** — specifically on measured *unlinked-that-should-link* cases (a real recall gap), never
  adopted speculatively, so the precision-first default is only relaxed against evidence.
- Fragmentation is measurable (`fragmentation_rate` against gold entity labels): resolution reduces it by
  linking surface variants T23b's exact-key clustering left separate (an alias) to the same CIK.
