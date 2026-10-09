# RuleWright: moving to rag-wright 0.3.0

What 0.3.0 changes for RuleWright, written from a read-only scan of the RuleWright repo (2026-10-09). It follows the
0.2.0 migration (`docs/specs/ingestion-hooks/rulewright-migration-0.2.0.md`); do that one first. Nothing here is
edited from the engine repo.

**Short version.** No import RuleWright uses today breaks. Three behaviour changes need a decision (section 2). The
interim rules given in the 0.2.0 Q&A are replaced by public calls (section 3), and the engine now has a pack SDK tier
for the moment RuleWright forks the reference pack into its own (section 4).

## 1. Nothing RuleWright imports breaks

The renamed engine internals (`_cosine`, `_is_bare_heading`, `_section_number`, `_post_json`,
`_INGEST_PARSE_DEADLINE_S`, `_RLM_GRANTED`, `_classify`, `_call_desc`) are not imported by RuleWright. The engine's
relevance `Condition` field was renamed `clause_type` -> `category`, but RuleWright's `clause_type` is its own
`GradedCondition` field and it never constructs the engine `Condition`. `ModelRole.OKF_ENRICHMENT` (removed) is not
used. The `answer_generator` privates RuleWright's tests import (`_PARTIAL_RE`, `_scrub_prose`) are unchanged.

## 2. Behaviour changes that need a decision

1. **Answer generation lost its contract wording unless you pass it back.** The engine's generation and relevance
   methods are domain-neutral now (ADR-0126 context, PS-R5a); the contract wording moved into the reference pack as
   guidance. RuleWright calls `rag_wright.capabilities.answer_generator.agenerate_answer` directly (7 imports), so its
   answers no longer get the contract guidance (the cap-and-carve-outs instruction, the "is this the clause type
   asked about" check) unless it passes `guidance=`:
   `agenerate_answer(query, evidence, model=..., guidance=contract_guidance("generation"))`, with
   `from rag_wright.packs.contracts.skills.guidance import contract_guidance`. The subgraphs RuleWright invokes
   (`intra_document_qa`, `relational_qa`, `typed_property_retrieval`) already pass it.
2. **Clause functions and citations change on re-ingest (ADR-0126).** A provision is now labelled by a vote over its
   operative (non-heading) spans and cited by the span most confident in that label, instead of by its heading.
   Measured on 510 CUAD contracts: clause-function accuracy 56.6% vs 46.8%. Clauses now cite operative sentences. The
   clause-extraction cache key includes the cited span, so a re-ingest re-extracts the clauses whose citation moved.
3. **A failed party extraction no longer drops the document (PS-R2).** The clauses and spans are written and the
   document is reported PARTIAL with a `graph` failure (`build_partial_entry` gained an optional `graph_failures`
   argument; RuleWright's three-argument calls are unaffected, and a `graph` kind appears in the `failures` list it
   already counts).

Also: Qwen3.8-27b on OpenRouter is no longer pinned to one provider (ADR-0125), so a provider outage no longer stalls
calls; the engine's MCP servers now refuse an unmigrated database loudly (run `scripts/migrate_span_fields.py`).

## 3. The 0.2.0 interim rules, replaced

| Interim rule (0.2.0 Q&A) | Now, in `rag_wright.api` |
|---|---|
| `ws._store` only to build a pack store | `pack_store(ws, ContractKGStore)` (any `cls(store, ...)`); `ws._store` stays private |
| parse uploads through an engine module or temp files | `parse_document_bytes` / `aparse_document_bytes`; `IngestSource(data=..., name=...)` for `build_ingestion` |
| meter and trace your own calls through `models.usage` / `models.tracing` | `record_usage(...)` into the active `measure_usage()` scopes; `traced_run(...)` / `traced_step(...)` |
| generate answers / judge relevance through `capabilities.*` | `agenerate_answer(query, evidence, *, ws, guidance=None)` and `ajudge_spans(spans, condition, *, ws, guidance=None)`, models from the workspace's roles |
| `ModelRole` from `models.profiles` | `ModelRole` |
| the property fleet only loads from an engine checkout | `RAG_MODELS_DIR` points every classifier at a models root (default: the engine checkout's `data/models`, else `./data/models`); `scripts/fetch_reference_models.py` fetches the reference weights from GCS with checksums (the agreed direction: RuleWright owns a copy and points `RAG_MODELS_DIR` at it) |

New on the API too: `kg_count` / `kg_delete` / `kg_update` and `kg_read(key_range=...)`, `StoreConfig.from_env()`,
`build_ingestion(unit_representative=..., chunk_discoverer=...)`, `default_chunk_discoverer(guidance=...)`. The
generated `docs/api/README.md` lists everything.

## 4. When RuleWright forks the reference pack: build it on `rag_wright.pack_sdk`

The agreed end state is RuleWright's own pack. The engine now declares what a pack may build on: `rag_wright.api` plus
`rag_wright.pack_sdk` (`docs/api/pack_sdk.md`; identifiers and provenance, the model seam, the LangGraph scaffold, the
store protocol, the generic capabilities a pack composes, parsing and chunking, capability plumbing), and the engine's
own reference pack is held to it by an import contract. A fork that keeps to those two imports survives engine
releases; RuleWright's seam today imports about 40 engine-internal modules (`store.arcadedb` 15 times,
`capabilities.answer_generator` 7, `models.profiles` 7, `capabilities.remote_encoders` 6, ...), and each of those is
now available from one of the two tiers.

## 5. Link the engine skills from the installed engine (PS-10)

Needs rag-wright 0.3.1 or later from PyPI: the 0.3.0 wheel left the skills out (an editable path dependency
has them either way).

The engine's Claude Code skills now ship in the package, at `rag_wright/.agents/skills/` (the convention docling and
fastapi use), version-matched to the engine: `using-the-rag-wright-engine`, `building-an-ingestion-capability`,
`authoring-a-capability`, `creating-evals`, `classifier-opportunity-analysis`, `setfit`, `laya` and
`qwen-vllm-modal` (with its Modal deploy script). RuleWright links none of them today. Its
`scripts/refresh_framework_graph.sh` already links every dependency's `.agents/skills/` from site-packages, but that
`find` misses the engine while it is an editable path dependency (site-packages holds only a `.pth` pointing at the
engine's `src/`). Resolve the engine's skills directory by import instead, which works in both modes, and link
them in the same step:

```bash
SKILLS=$(uv run python -c "import pathlib, rag_wright; print(pathlib.Path(rag_wright.__file__).parent / '.agents' / 'skills')")
for d in "$SKILLS"/*/; do ln -sfn "${d%/}" ".claude/skills/$(basename "$d")"; done
```

## 6. Verify

Run RuleWright's suite against the engine checkout (editable path) and its live engine-seam test; re-ingest one
contract and compare its clauses (expect the section 2 changes, not errors).
