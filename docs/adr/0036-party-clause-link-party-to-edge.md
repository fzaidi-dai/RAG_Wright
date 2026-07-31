# ADR-0036: The `Party <-> Contract` unifying link (`PARTY_TO` edge)

- Status: accepted
- Date: 2026-07-31
- Related: ADR-0033 (unified contract KG, three legs, one graph), ADR-0035 (graph_extraction re-backed with
  GP-1B), FR-S.1 (one store, chunk and entities connect), KG-7.

## Context

The unified contract KG (ADR-0033) is served as three scoped-query legs over ONE graph, but it was built as
two separately-populated node families that never got connected:

- the **typed Clause KG** -- `Clause` nodes (`clause_id = contract_id:index:hash`) + typed property edges, and
- the **party graph** -- `Entity` nodes (one per CIK, deduped) + `CONTRACTS_WITH` / `AFFILIATE_OF` edges,
  populated by GP-1B (Leg C, real recall 0.991).

Both live in one store (`ragwright_cuad`), but nothing links a party to the clauses/contracts it is a party to.
`Contract -> Clause` is already implicit (`clause_id` encodes `contract_id`, a many-to-one composition, so
Contract->Clauses is a free key-range query). The genuinely missing link is `Party -> Contract`. Both graphs
are already populated, so this must be derivable WITHOUT re-ingesting either.

## Decision

**Add a `PARTY_TO` edge (`Entity -> Contract`), derived by a link pass over the already-populated nodes.**

- **Edge, not an id/field.** `Party -> Contract` is many-to-**many**: a deduped `Entity` (one CIK node) is a
  party to many contracts, and `entity_id` is a global identity (the CIK), not contract-scoped. An id-prefix or
  scalar field can only encode a single owner (as `Clause` does); a list field is not range-queryable and
  breaks the dedup. An edge is the correct representation, and it stays extensible for a future per-clause party
  ROLE attribute.
- **Join by provenance, on the canonical id.** Each `Entity.chunk_id` (`<source_doc_id>:idx:hash`) carries the
  `source_doc_id` of the contract GP-1B extracted the party from; post-HYG-1/HYG-2 every graph shares the one
  canonical `_` slug, so that `source_doc_id` is an exact match for a `Contract.contract_id`. A
  `(entity_id, contract_id)` pair is deduped. (The original design matched `Contract.parties_json` names to
  `Entity.name` by `normalize_entity_name`, but the live `parties_json` is empty on every contract, so it
  linked nothing -- the smoke run caught this; the provenance id join is what the data supports.)
- **No re-ingest.** The derivation only reads the populated `Contract` + `Entity` nodes and writes edges; it
  parses/chunks/extracts nothing. `write_party_contract_links` clears the `PARTY_TO` layer first, so re-linking
  is idempotent and re-derivable.
- **Honest gap surfaced.** An `Entity` whose contract has no `Contract` node -- the corpus-coverage gap: most
  CUAD contracts are party-extracted but not clause-ingested (CUAD-FULL-COVERAGE) -- is COUNTED
  (`unmatched_parties`), never silently dropped.
- **Known limitation.** GP-1B upserts one `Entity` node per resolved party, so a multi-contract party keeps
  only its last-written `chunk_id`; the provenance join links it to that one extraction-source contract, not
  every contract it signed. Full many-to-many coverage needs the per-(contract, party) mention data (the GP-1B
  cache) -- a later enhancement alongside the party-role layer.
- **Registered** as the `party_clause_linking` `function` (twofold: canonical slug + ARD manifest + register).

## Consequences

- The two node families become one traversable graph: `Party -PARTY_TO-> Contract`, then `Clause`s via the
  `clause_id` key-range -- both "all clauses across a party's contracts" and "the parties to a clause's
  contract". Leg A / B / C now share real connective tissue, not just a shared store.
- Store surface: a new `PartyTo` edge type in `ensure_schema`, `all_contracts` / `all_entities` readbacks, and
  `write_party_contract_links`. No node schema or identifier scheme changes (not an ask-first identifier change).
- Contract->Clause stays edge-free (the `clause_id` key-range already gives it); only the missing many-to-many
  link is added. A future per-clause party ROLE layer would extend the `PARTY_TO` edge with attributes rather
  than add nodes.
- Unblocks **LG-3d** (the ingestion pipeline can run this link step after writing both node families).
