# Entity resolution & disambiguation

The entity graph is only useful if the same real-world entity is **one node**, however its name varies across the
corpus. Ingestion does this in two domain-neutral phases, then links each entity to a canonical `entity_id`.

## The two phases

1. **Disambiguation (coreference).** Mentions are clustered so surface variants of the same entity group together
   (a two-channel dedup, ADR-0004). This is domain-neutral and lives in the engine (`capabilities.disambiguation`).
2. **Resolution.** Each cluster is matched to a canonical id by an injected **resolver** (`capabilities.entity_resolution.resolve_entities(..., resolver=…)`). The matching strategy is the
   domain's concern; only the surface-form lookup is delegated to the resolver (ADR-0013).

The resolver is **closed-world**: an unknown surface form resolves to `None` (an *unlinked* entity — expected for
private/unknown entities), **never a fabricated id**. Unlinked is a valid, honest outcome.

## The generic default

The engine ships a domain-neutral `EntityRegistry` (`rag_wright.ontology.registry`): a closed-world set of canonical
entities indexed by **normalized surface form**, with the normalizer **injectable** (`EntityRegistry(normalize=…)`,
default a generic slug). `resolve(surface)` returns the canonical `entity_id` or `None`. This exact-normalized match
is high-recall for entities the registry knows and fabricates nothing for those it doesn't (ADR-0067: the registry
is generic; a domain's canonical ids and their normalization are the domain's concern).

## Configuring it for your domain

1. **Declare the entity types** in your pack (`eng:EntityNodeType` / `eng:EntityRelationshipType` —
   [ontology authoring](ontology-authoring.md)).
2. **Provide a resolver.** Either populate an `EntityRegistry` with your canonical entities
   (`registry.add(RegistryRecord(entity_id=…, surface_forms=[…]))`) and optionally inject a domain `normalize=`, or
   implement the `EntityResolver` protocol yourself (a custom strategy, or a call to an external authority service)
   honoring the closed-world contract.
3. **Wire it into your ingestion graph's resolve step** — pass your resolver to `resolve_entities`. The
   clustering/dedup stays the engine's generic disambiguation; only the lookup is yours.

### The `entity_id` rule

`entity_id` is the canonical registry id and is **load-bearing**: a non-canonical id fragments the entity graph
across surface-form variants (and breaks the join to the records that cite the entity). Resolve to one canonical id
per entity; keep it stable.

## Known gap (new-domain exposure) — tracked for the engine

Today, `entity_resolution` and `entity_disambiguation` are **internal steps of the ingestion pipeline** (ADR-0118,
EP-CORE-1b-iii) — not invocable-by-name capabilities — and there is **no public-API seam** on `rag_wright.api` (nor
an `EngineConfig` hook) to supply your registry/resolver. A new domain wires its resolver inside its own ingestion
subgraph (which is the capability-authoring path, so it works), but there is no first-class "bring your resolver"
configuration yet. This is logged in the engine-gaps register (PREP-4.7); a candidate is a resolver/registry hook on
`EngineConfig` or an `rag_wright.api` helper to register a domain resolver.

## Reference example

The reference pack populates the registry with SEC EDGAR entities and resolves parties to their CIK — that EDGAR/
CIK choice is **the reference domain's**, not an engine assumption. See the reference ingestion's resolve step as
the template. Next: [authoring capabilities](authoring-capabilities.md).
