"""OKF (Open Knowledge Format) bundle compile pipeline (FR-K.1-K.4, T46).

Compiles the ingested chunk corpus into an OKF v0.1 conformant bundle for the embedding-free
knowledge-navigation path (FR-K). Not a re-chunk: bodies come from the T40 chunk-text sidecar and
`chunk_id` identity is unchanged (FR-S.2). Three parts:

  - `enrich`   : the one model-bearing step (category + one-line description per clause), gated and
                 concurrent. Uses a cheap model for this simple task only (ADR-0023).
  - `compile`  : deterministic bundle write (tree, frontmatter, index.md, content-hash gate).
  - `lint`     : deterministic conformance linter (frontmatter, type, links, coverage).
"""
