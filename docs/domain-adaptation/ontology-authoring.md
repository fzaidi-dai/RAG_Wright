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

The store creates the vertex and edge types your pack declares. The generic reader is
`rag_wright.ontology.pack_schema.load_kg_schema(path)`, called by the store's `ensure_pack_schema(ttl)`, which
`ensure_schema()` calls only when a pack is configured (`EngineConfig.pack`). It creates the `eng:KgVertexType`
vertex types with their properties and unique indexes, and the `eng:KgStructuralEdge` edge types; nothing else in
the pack is read by the engine. A minimal, domain-neutral example:

```turtle
@prefix eng:  <https://ragwright.local/ontology/engine#> .
@prefix ex:   <https://example.com/mydomain#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# a KG vertex type the store creates (name, its typed properties, and a unique-index property)
ex:RecordNode a eng:KgVertexType ;
    eng:vertexName "Record" ;
    eng:kgProperty "record_id:STRING", "category:STRING", "span_id:STRING", "confidence:STRING" ;
    eng:uniqueIndexOn "record_id" .

# a structural edge between vertices
ex:RelatesToEdgeDecl a eng:KgStructuralEdge ; eng:edgeName "RELATES_TO" .

# optional: entity-graph taxonomy labels (read only by the reference pack's own loader today)
ex:Organization a eng:EntityNodeType ; rdfs:label "Organization" .
ex:LinkedTo a eng:EntityRelationshipType ; rdfs:label "Linked To" .
```

- **Declare every type your extractor writes.** The store does not create types on write, so a `kg_write` to an
  undeclared vertex or edge type fails.
- **Record vertices carry provenance.** Declare `span_id:STRING` and `confidence:STRING` on each record type: the
  engine's `check_extraction` requires each extraction to cite a span of its unit (`span_id`), and any `confidence`
  must be a `ConfidenceTag` (`EXTRACTED`, `INFERRED` or `AMBIGUOUS`) ([KG construction](kg-construction.md)).
- **`eng:EntityNodeType` / `eng:EntityRelationshipType` are optional.** Only the reference pack's loader and code
  generator read them; the engine's `EntityNode.entity_type` and `RelationshipFact.relationship_type` are opaque
  strings your domain names (`rag_wright.contracts.graph`).

The generic infra types (`Chunk`, `Span`, `Entity`, `Relationship`, `Mentions`, `Document`, `EmbeddedIn`,
`AttachedTo`) stay in engine code; only your *domain* vertex/edge types are pack-declared.

### 2. Closed value sets

Enumerate the allowed values for each typed property of your domain — the vocabulary a classifier classifies into
and extraction targets. Author them as your own classes / SKOS concepts. The engine does not read them: your
capabilities read them from the pack (with `rdflib`), so the vocabulary is shared by ingestion and query from one
source.

### 3. Constraints (SHACL)

Express applicability and cardinality as SHACL `sh:NodeShape`s: which properties apply to which unit, how many
values are allowed, and any domain-specific polarity. Your capabilities validate records against them with
`pyshacl`. `build_ingestion` has no SHACL gate; the reference pack runs its own symbolic validation gate inside its
contract pipeline (engine gap G10).

### 4. Mappings and synonyms

Use SKOS (`skos:altLabel` for synonyms, `skos:broader` for roll-ups) so surface variants normalize to your
canonical values — again, read from the one pack, used everywhere.

### 5. Decision criteria (for a decision model)

When a capability asks a typed decision model (Jev, ADR-0119) to choose among options, the options and their
criteria are domain knowledge, so they live in the pack (ADR-0066), not in the capability code. Author three
things: a criterion property (one line per option), an order property (a stable option order), and one
instructions string stated once before the items. The reference pack's residual-value roles are the pattern:

```turtle
@prefix ex:   <https://example.com/mydomain#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

ex:decisionCriterion a owl:DatatypeProperty ; rdfs:range xsd:string .
ex:roleOrder         a owl:DatatypeProperty ; rdfs:range xsd:integer .
ex:SampleRole        a owl:Class .

ex:sampleRoleQuestion ex:questionInstructions "For each numbered candidate, decide what it is in this record." .
ex:role_batch_id a ex:SampleRole ; skos:prefLabel "batch_id" ; ex:roleOrder 0 ;
    ex:decisionCriterion "the identifier of the production batch the record describes" .
ex:role_none     a ex:SampleRole ; skos:prefLabel "none"     ; ex:roleOrder 1 ;
    ex:decisionCriterion "none of these" .
```

In the reference pack these are `cbr:ResidualRole` + `cbr:decisionCriterion` + `cbr:roleOrder` +
`cbr:residualRoleQuestion` in `contract_bridge.ttl`, and `cmp:decisionCriterion` on vocabulary members in
`compliance_bridge.ttl`. The engine has no generic reader for criteria yet (engine gap G7): your capability reads
them with a few lines of `rdflib` (labels and criteria in order) and builds the `questions` it sends to
`jev_decision` ([classification & decision models](classification-and-decision-models.md)). Include a `none`
option so the model can decline.

## How the pack is loaded

```python
from rag_wright.api import EngineConfig, StoreConfig, open_workspace
cfg = EngineConfig(store=StoreConfig(...), pack="packs/mydomain.ttl")   # None = no domain pack (neutral schema)
ws = open_workspace(cfg, corpus="mydomain")   # ensure_schema() creates your declared vertex/edge types
```

The engine reads only the schema vocabulary (the vertex and edge declarations). Your capabilities read your own
value sets, shapes, mappings and criteria (with `rdflib` / `pyshacl`), as the reference pack does for its packs
(its loader, `rag_wright.packs.contracts.ontology.loader`, is reference-pack code). Changing the domain = editing the `.ttl`.

## Source of truth + no drift (ADR-0066)

The `.ttl` is the single authoritative source of domain knowledge. Where a consumer needs a form the ontology can't
express (e.g. an LLM prompt overlay), you **generate** that artifact from the pack and have CI diff it (regenerate →
diff → fail on any delta) — never hand-edit a second copy into code. Solve staleness by enforced synchronization,
not by moving truth into Python. Validate the pack itself with SHACL (`pyshacl`); the reference pack's
`tests/ontology/test_ttl_is_source_of_truth.py` shows the drift-guard pattern to mirror.

## Worked example

The reference pack's bridges are the complete example of every section above:
`rag_wright/packs/contracts/ontology/contract_bridge.ttl` (vertex/edge declarations, value sets, SHACL shapes, SKOS roll-ups —
grounded in external standards *as that domain's choice*, not an engine requirement), `compliance_bridge.ttl`, and
the domain pack `rag_wright/packs/compliance/ontology/packs/ftc_16cfr255.ttl`. Next: [KG construction](kg-construction.md)
populates the schema this pack declares; [entity resolution](entity-resolution.md) canonicalizes the entity-graph types.
