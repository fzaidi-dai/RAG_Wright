# ADR-0010: transformers pinned below 5 for the FlagEmbedding reranker

Date: 2026-07-11. Status: Accepted. Records why `transformers` is capped at `>=4.44.2,<5` in
`pyproject.toml`, so a future dependency upgrade does not silently re-break the reranking capability.

## Context

The reranking capability (T22, FR-C.4) uses `FlagEmbedding.FlagAutoReranker` over `BAAI/bge-reranker-v2-m3`.
`FlagEmbedding` 1.4.0 declares `transformers<6.0.0,>=4.44.2`, so the resolver picked the latest allowed,
**transformers 5.8.1**. On that version the live cross-encoder run failed at inference:

```
AttributeError: XLMRobertaTokenizer has no attribute prepare_for_model
```

transformers 5.x removed `prepare_for_model` from the tokenizer API, but FlagEmbedding's reranker
collation still calls it (both the slow and the fast XLM-RoBERTa tokenizer lack it on 5.x, so there is no
code-side workaround). FlagEmbedding 1.4.0's declared `<6` bound is therefore stale: it advertises a 5.x
range its reranker does not actually support. The bi-encoder embedding path (T19, BGE-M3) does not call
`prepare_for_model`, which is why embedding worked on 5.x and only reranking broke.

## Decision

Pin `transformers>=4.44.2,<5` in `pyproject.toml`. The resolver takes the last 4.x line (4.57.6), where
`prepare_for_model` exists and the reranker works.

The cap is compatible with every other consumer of transformers in the tree (checked before pinning):

| Package | transformers constraint | satisfied by `<5` |
|---|---|---|
| docling-ibm-models (darwin) | `>=4.42.0,<5.9.0`, excl. 5.0–5.3 | yes (allows 4.42+) |
| FlagEmbedding | `>=4.44.2,<6.0.0` | yes |
| sentence-transformers | `>=4.41.0,<6.0.0` | yes |

`uv add "transformers>=4.44.2,<5"` resolved cleanly (transformers 5.8.1 → 4.57.6; huggingface-hub
adjusted 1.22.0 → 0.36.2 as a resolution consequence). Full default suite stays green (294 passed).

## Consequences

- The live reranker (`-m rerank`) passes: the real cross-encoder ranks a relevant passage above an
  irrelevant one.
- **Do not lift this cap** until FlagEmbedding ships a release whose reranker supports transformers 5.x
  (its `prepare_for_model` call is removed or guarded). If a future `uv lock` or a dependency bump wants
  transformers 5.x, re-run the live `-m rerank` test before accepting it — the hermetic suite uses a stub
  reranker and will not catch this regression.
- This is a dependency-constraint change, taken with human approval per the ask-first rule (CLAUDE.md).
