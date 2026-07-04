"""The single ArcadeDB store behind the query-skill seam (FR-S.1, FR-S.5).

Holds both the hybrid retrieval index and the knowledge graph in one multi-model database.
Reached only through the query-skill interface so the store implementation is swappable
(Graphify for a prototype graph, and the eval-gated LanceDB fallback for the retrieval leg).
"""
