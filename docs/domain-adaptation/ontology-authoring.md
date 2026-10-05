# Authoring a domain `.ttl` pack

A domain is described by an ontology pack: a single Turtle (`.ttl`) file that declares your domain's **knowledge** —
the KG schema, closed value sets, constraints, and synonyms — **declaratively, never in Python** (ADR-0066). Code
holds mechanism (the pipeline, the gates, the router); the pack holds what the domain *means*. A new domain is a new
pack, not an engine edit.

The engine provides a small, domain-neutral declaration vocabulary (the `eng:` namespace,
`https://ragwright.local/ontology/engine#`). You use it to declare your own types; your subjects and vocabulary are
yours. The bundled [reference pack](../reference-pack.md) is a full worked example of everything below — read it
alongside this guide.

## What a pack declares

### 1. The KG schema (vertex + edge types)

The store creates the vertex and edge types your pack declares (`ontology.loader.load_kg_schema` →
`store.ensure_schema()`). A minimal, domain-neutral example:

```turtle
@prefix eng: <https://ragwright.local/ontology/engine#> .
@prefix ex:  <https://example.com/mydomain#> .

# a KG vertex type the store creates (name, its typed properties, and a unique-index property)
ex:RecordNode a eng:KgVertexType ;
    eng:vertexName "Record" ;
    eng:kgProperty "record_id:STRING", "category:STRING", "span_id:STRING" ;
    eng:uniqueIndexOn "record_id" .

# a structural edge between vertices
ex:RelatesToEdgeDecl a eng:KgStructuralEdge ; eng:edgeName "RELATES_TO" .

# entity-graph types (for entity resolution — see entity-resolution.md)
ex:Organization a eng:EntityNodeType ; rdfs:label "Organization" .
ex:LinkedTo a eng:EntityRelationshipType ; rdfs:label "Linked To" .
```

The generic infra types (`Chunk`, `Span`, `Entity`) stay in engine code; only your *domain* vertex/edge types are
pack-declared.

### 2. Closed value sets

Enumerate the allowed values for each typed property of your domain — the vocabulary a classifier classifies into
and extraction targets. Author them as your own classes / SKOS concepts; the loaders read them so the vocabulary is
engine-wide (ingestion AND query) from one source.

### 3. Constraints (SHACL)

Express applicability and cardinality as SHACL `sh:NodeShape`s (`ontology.loader.load_shapes_graph`, validated with
`pyshacl`): which properties apply to which unit, how many values are allowed, and any domain-specific polarity. The
symbolic validation gate enforces these at ingestion.

### 4. Mappings and synonyms

Use SKOS (`skos:altLabel` for synonyms, `skos:broader` for roll-ups) so surface variants normalize to your
canonical values — again, read from the one pack, used everywhere.

## How the pack is loaded

```python
from rag_wright.api import EngineConfig, StoreConfig, open_workspace
cfg = EngineConfig(store=StoreConfig(...), pack="packs/mydomain.ttl")   # None = the bundled reference pack
ws = open_workspace(cfg, corpus="mydomain")   # ensure_schema() creates your declared vertex/edge types
```

The loaders (`rag_wright.ontology.loader`) read the pack's vocabulary, schema, constraints, and mappings; the
ingestion and query capabilities are parameterized by them. Changing the domain = editing the `.ttl`.

## Source of truth + no drift (ADR-0066)

The `.ttl` is the single authoritative source of domain knowledge. Where a consumer needs a form the ontology can't
express (e.g. an LLM prompt overlay), you **generate** that artifact from the pack and have CI diff it (regenerate →
diff → fail on any delta) — never hand-edit a second copy into code. Solve staleness by enforced synchronization,
not by moving truth into Python. Validate the pack itself with SHACL (`pyshacl`); the reference pack's
`tests/ontology/test_ttl_is_source_of_truth.py` shows the drift-guard pattern to mirror.

## Worked example

The reference pack's bridges are the complete example of every section above:
`rag_wright/ontology/contract_bridge.ttl` (vertex/edge declarations, value sets, SHACL shapes, SKOS roll-ups —
grounded in external standards *as that domain's choice*, not an engine requirement), `compliance_bridge.ttl`, and
the domain pack `packs/ftc_16cfr255.ttl`. Next: [KG construction](kg-construction.md) populates the schema this
pack declares; [entity resolution](entity-resolution.md) canonicalizes the entity-graph types.
