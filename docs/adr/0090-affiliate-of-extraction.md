# ADR-0090: extract AFFILIATE_OF (corporate affiliation) during ingestion

**Status:** accepted · **Date:** 2026-09-09 · **Issue:** engine 0027 (RuleWright) · **Builds on:** ADR-0035 (GP-1B party extraction), ADR-0036/KG-7 (the entity graph), ADR-0045 (client-side tag-parse), ADR-0066 (ontology = knowledge, code = mechanism)

## Context

`RelationshipType.AFFILIATE_OF` was declared in the ontology, `write_graph` writes any `relationship_type` generically, and `graph_query` traverses any `RelationshipType` — but **nothing ever produced the edge**. So "what is our exposure to the Acme group?" was unanswerable: an affiliate's contract was indistinguishable from a contract that does not exist. `CONTRACTS_WITH` is *structural* (derived from the signing-party list, no LLM), but affiliation is *stated in the text* ("Acme Holdings Ltd, an affiliate of Acme Corp"), so it needs a text-reading extraction that never existed.

## Decision

Extract corporate affiliations once per contract, from the preamble, alongside party extraction, and emit `AFFILIATE_OF` facts.

- **A per-contract affiliation extraction** (not per chunk): affiliations, like parties, live in the preamble; `aper_contract_graph_extraction` gains an optional `aaffiliations_fn` + a separate `affil_dir` cache. Its result is appended to the parties' `ExtractionResult`.
- **Lexical pre-filter** (`_AFFILIATION_CUE_RE`): if the preamble contains no affiliation cue word (`affiliate`, `subsidiary`, `parent company`, `wholly-owned`, `under common control`, `a division of`, `owned by`), no LLM call is made. Most contracts state no affiliation, so added ingestion cost is ~zero except where it matters. A spurious cue hit just costs a call that returns nothing; a miss is a false-negative.
- **Extraction** via client-side tag-parse (ADR-0045) over the preamble into `Affiliations(list[Affiliation{organization, affiliate_of}])`, on the graph-extract model (`granite-4.2-8b`, `RAG_GRAPH_EXTRACT_MODEL`). Degrades to no affiliations on any parse failure (never raised).
- `affiliations_to_extraction` emits an `EntityMention` for **both** orgs (so both endpoints resolve to nodes) + an `AFFILIATE_OF` `RelationshipFact`. **No entity merging** — an affiliate is a separate legal entity; only the edge between the two nodes is added (the issue's explicit requirement; merging would misstate who owes what).
- Default ON; `RAG_INGEST_AFFILIATIONS=0` disables it. Separate cache dir (`graph_affiliations/`) so existing party caches need no format migration.
- ADR-0066: the relationship **type** (`AFFILIATE_OF`) is ontology-declared; the cue words + extraction prompt are the code-side prompt-engineering/mechanism overlay.

## Consequences

- Corporate affiliation is answerable: `graph_query(..., relationship_type=AFFILIATE_OF)` now has edges to traverse. Live-verified on the issue's case: "Acme Holdings Ltd, an affiliate of Acme Corp" → `AFFILIATE_OF(Acme Holdings Ltd → Acme Corp)` with both orgs as `ORGANIZATION` mentions; a plain two-party contract produces no affiliation and no LLM call.
- The write + query sides were already generic, so no store or traversal change was needed — only extraction.
- The product decides whether to present a corporate group or a single entity (`affiliates_included`); the engine returns the fact. Same division of labour as issue 0023.
- The affiliate's org node exists whether or not it is also a signing party (the extractor emits its mention), so an affiliate named only in an affiliation clause is still a node.
