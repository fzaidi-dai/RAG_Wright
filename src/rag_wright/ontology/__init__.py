"""Ontology and entity-registry derivation (FR-C.8).

Pulls the entity and relationship types (as Pydantic models) and the populated entity
registry from the Data Catalog (Nessie or equivalent). Where the catalog does not exist,
extraction degrades to the lightweight path plus an open-ended language model (assumption 3).
"""
