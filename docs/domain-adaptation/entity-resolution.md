# Entity resolution & disambiguation

The entity graph is only useful if the same real-world entity is **one node**, however its name varies across the
corpus. The engine provides two domain-neutral phases, then links each entity to a canonical `entity_id`.

These are building blocks, not stages `build_ingestion` runs: the generic pipeline builds no entity graph unless you
pass a `document_hook` that does ([KG construction](kg-construction.md)). The reference pack's contract pipeline
runs them as its own steps.

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

1. **Name your entity and relationship types.** They are opaque strings on `EntityNode.entity_type` and
   `RelationshipFact.relationship_type`. Listing them in your pack (`eng:EntityNodeType` /
   `eng:EntityRelationshipType`) is optional: only the reference pack's loader reads those today
   ([ontology authoring](ontology-authoring.md)).
2. **Provide a resolver.** Either populate an `EntityRegistry` with your canonical entities
   (`registry.add(RegistryRecord(entity_id=…, surface_forms=[…]))`) and optionally inject a domain `normalize=`, or
   implement the `EntityResolver` protocol yourself (a custom strategy, or a call to an external authority service)
   honoring the closed-world contract.
3. **Run it in a `document_hook`**: pass your resolver to `resolve_entities`. The clustering/dedup stays the
   engine's generic disambiguation; only the lookup is yours.

### Recipe: an entity graph in a `document_hook`

```python
import hashlib

from rag_wright.capabilities.disambiguation import disambiguate
from rag_wright.capabilities.entity_resolution import resolve_entities
from rag_wright.capabilities.graph_storage import GraphWriter
from rag_wright.contracts.identifiers import ChunkId
from rag_wright.subgraphs.graph_extraction import build_graph_extraction

graph = build_graph_extraction(my_entity_extractors)   # required: your stack of graph extractors

async def entity_graph_hook(ws, sd, chunks):
    results = [graph.invoke({"chunk_id": ChunkId.of(sd.source_doc_id, c.chunk_index, c.text), "text": c.text})["result"]
               for c in chunks]
    clusters = disambiguate(results)
    resolution = resolve_entities(clusters, results, resolver=my_resolver)
    content_hash = hashlib.sha256(sd.text.encode("utf-8")).hexdigest()
    GraphWriter(ws._store, checkpoint_dir=cache_dir).write_document(sd.source_doc_id, content_hash, resolution)

pipeline = build_ingestion(extractor, document_hook=entity_graph_hook)
```

- Each graph extractor implements the graph-extraction `Extractor` protocol in `rag_wright.contracts.extraction`
  (`name`, and `extract(chunk_id, text) -> ExtractionResult`); this is a different protocol from the ingestion
  `Extractor` you pass to `build_ingestion`. `extractors` is required: the reference pack's party-extraction stack
  is reference-pack code, not an engine default.
- The generic graph targets are `EntityNode` and `RelationshipFact` in `rag_wright.contracts.graph`; their type
  fields are opaque strings your domain names.
- `GraphWriter` skips a document whose content hash it has already written.
- None of these is exported from `rag_wright.api` yet, and `GraphWriter` needs the workspace's internal store (engine
  gap G1). Run the graph off the event loop (`asyncio.to_thread`) if your extractors are slow.

### The `entity_id` rule

`entity_id` is the canonical registry id and is **load-bearing**: a non-canonical id fragments the entity graph
across surface-form variants (and breaks the join to the records that cite the entity). Resolve to one canonical id
per entity; keep it stable.

## Known gap (new-domain exposure), partly closed: tracked for the engine

`build_ingestion(document_hook=)` is now a public per-document seam, so a domain runs its entity graph and resolver
there (the recipe above). Still open (engine gap G1): `entity_resolution` and `entity_disambiguation` are internal
steps (ADR-0118, EP-CORE-1b-iii), not invocable-by-name capabilities; there is no resolver/registry hook on
`EngineConfig` or `rag_wright.api`; and `build_graph_extraction`, `disambiguate`, `resolve_entities` and `GraphWriter`
are imported from engine modules rather than `rag_wright.api`.

## Reference example

The reference pack populates the registry with SEC EDGAR entities and resolves parties to their CIK — that EDGAR/
CIK choice is **the reference domain's**, not an engine assumption. The reference pack (`rag_wright.packs.contracts`)
runs extraction, disambiguation and resolution inside its own contract pipeline; read it as an example.
Next: [authoring capabilities](authoring-capabilities.md).
