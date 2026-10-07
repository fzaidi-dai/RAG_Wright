# ING-5: documentation audit, what changed and why (2026-10-07)

Every engine-facing doc was checked claim by claim against the code after ING-1 to ING-8e and the API cleanup;
runnable snippets were executed (no paid model calls; scratch ArcadeDB databases, dropped afterwards). The audit is
now enforced: `tests/arch/test_doc_references.py` checks every `rag_wright.*` symbol, repo path, relative link and
Python snippet (engine imports and keyword arguments) in the README, `docs/`, `docs/domain-adaptation/`, the
product-starter templates, the skills and `CLAUDE.md`, with a proof test that it catches each kind of stale reference.

## Found by the audit and fixed in code

- **Concurrent writes were silently lost (data loss).** ArcadeDB answers concurrent writes to one page with a
  `ConcurrentModificationException` ("Please retry the operation"); the store retried nothing, so the default
  2-document concurrent ingest dead-lettered documents and dropped spans (a scratch reproduction kept 36 of 200 spans
  and 65 of 200 records under 8 writers). The store now retries that conflict with bounded, jittered backoff on every
  command and transaction (a failed one is rolled back whole, so the retry is safe). Tests:
  `tests/store/test_concurrent_writes.py` (live: 120/120 spans and records) and the skill example below (two documents
  at `document_concurrency=2`, no span failures).
- **No test imported any manifest's `impl_ref`** (the authoring guardrail's kind check reads the kind from the same
  manifest). Added `test_every_registered_impl_ref_resolves_to_a_callable`.
- **Manifest mistakes fail where they are written.** `CapabilityManifest` now raises `ValueError` for
  `response_bounds` on a non-callable kind (`author()` used to drop them silently), and `author()`'s non-canonical-slug
  error says how to fix it (`register_canonical_slugs` in the pack's `register()`). Tests:
  `tests/capabilities/test_manifest_validation.py`.
- Stale code text: the canonical-slug comment in `capabilities/registry.py` (named the removed `reference.pack`); the
  compliance `ajev_extract_regulation_section` docstring (said applicability/evidence stay empty; a gated residual call
  fills them); the quickstart prints `cited clauses:` (the citations are clause ids, not span ids).

## New

- `python-dotenv` is a declared dependency (`pyproject.toml`): `examples/quickstart.py`,
  `scripts/migrate_span_fields.py` and the pack MCP servers import it, and it was only installed transitively.
- The `building-an-ingestion-capability` skill: the stage-ownership table, deciding the unit, declaring record types
  in the pack `.ttl`, the extractor and its provenance rule, tuning with `evaluate_ingestion`, spreadsheets, tables and
  embedded files, a definition of done. Its worked example is executed verbatim by
  `tests/skills/test_ingestion_skill_example.py` (evaluate, then a concurrent two-document ingest, cited records).
- Engine gaps G13 (disambiguation's normalize/reject rules are contract-party rules in generic Python, not injectable;
  `RegistryRecord.ticker` is a SEC field) and G14 (ARD `publish` / `publish_all` are not on `rag_wright.api`).

## Docs (per area; the claim, and what the code says)

- **CLAUDE.md**: the capability spec is `SPEC.md` (there is no `RAG_Capability_Spec.md`; 5 references, including the
  start-of-session reading list); the import-linter rule describes the ING-8c contracts (everything outside
  `rag_wright.packs` is generic; a new generic top-level package must be added to `source_modules`); the new skill and
  the doc-reference check are listed.
- **README / installation / configuration / ArcadeDB_Local**: `uv add 'rag-wright[ner]'` (not `uv pip install`); the
  post-0.1.0 changes include the `rag_wright.packs` move, with the breaking-changes record linked; the JVM heap is
  `ARCADEDB_OPTS_MEMORY` (read at container start), not `JAVA_OPTS`; a new "upgrading a database" section (the
  unmigrated-Span refusal and `scripts/migrate_span_fields.py`); `EngineOptions.packs` and `ContractIngestOptions`
  (a wrong type raises `TypeError`); OpenRouter routing env vars attributed to the code that reads them; `ARCADEDB_*`
  serve `from_env` and the scripts, not `open_workspace`.
- **concepts / architecture / ARCHITECTURE_OVERVIEW / reference-pack**: the store is fully generic after ING-8e (pack
  types come from `ContractKGStore` / `ComplianceStore`, including `Requirement`); `okf` is generic; the extractor
  provenance rule as `check_extraction` enforces it; Chunk dense vectors embed the summary, Span vectors the span text.
- **quickstart / contract_pipeline_explainer / corpus_ingest_recipe / OBSERVABILITY / playbook**: answers are cited by
  clause id; intra-document QA serves from the knowledge graph and BGE-reranks (no hybrid search); real imports in the
  snippets; party extraction is already deadline-bounded; the `dg_extraction` path is the pack's; the playbook banner
  reflects ING-8c done.
- **domain-adaptation**: entity resolution described as the code does it (disambiguation clusters by normalized name
  and type with contract-party rules; the two-channel dedup is in resolution); `RegistryRecord` fields corrected
  (`entity_id`, `canonical_name`, `aliases`, `ticker`); the `document_hook` recipe made runnable (executed with stubs);
  the slug rule (registration takes any slug; publishing ARD requires a canonical one); `check_extraction` stated
  precisely; one pack `.ttl` per workspace (G9); the `build_ingestion` recipe executed live (records upserted, edges
  duplicated on re-ingest as G8 says, caches reused).
- **product-starter templates**: the remaining API exception is the entity-resolution building blocks and their
  contracts (G1); `kg_write` / `kg_edges` listed; the new skill added; missing FILL placeholders listed.
- **skills**: `authoring-a-capability` (imports from `rag_wright.api`; pack homes after ING-8c; what the invoker and
  the guardrail actually check; the two import contracts; `response_bounds` on an `agent_skill` is rejected); `using-the-rag-wright-engine` (`pack=` instead of the store's `ensure_pack_schema`; only database-style
  tables split per row; an unregistered `jev_decision` raises `KeyError`, a missing key `RuntimeError`);
  `classifier-opportunity-analysis` (the compliance residual call for applicability and evidence; 277 calls, 276 of
  them LLM); `setfit` (same count); `laya` (the fine-tune runs in the Laya repo's own environment; the real OOM knobs;
  `uv run`). `creating-evals` and `qwen-vllm-modal` checked out unchanged.

## Raised, not changed (decisions for later)

- Whether release-please treats a `refactor(...)!:` commit as a breaking change was not verified.
