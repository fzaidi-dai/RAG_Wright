# RuleWright handoff: "Qwen everywhere" config (RAG_MODEL_ALL now covers extraction)

Date: 2026-09-11 · on `origin/main` (commit `77b4acb`) · **No API change — exposed model arguments still win.**

---

## What changed

`RAG_MODEL_ALL` (the point-every-role-at-one-model knob) now **also covers the two extraction surfaces** — clause-property extraction (ingest) and claim/subject extraction (compliance/query). Previously those read a hardcoded granite literal and ignored `RAG_MODEL_ALL`, so an env-only "Qwen everywhere" left them silently on granite. Fixed: they resolve **explicit `extract_model` arg > `RAG_MODEL_ALL` > granite default** — parallel to `model_for`.

Your exposed model arguments are unaffected: an explicit `extract_model` (or `graph_extract_model`, `judge_model`, etc.) passed to any entrypoint still wins over `RAG_MODEL_ALL`.

## Your complete "Qwen everywhere" config (Route 1, env)

```bash
RAG_MODEL_ALL=qwen/qwen3.8-27b        # ALL model_for roles (judge, function-classify, chunk-refine, generation)
                                       #   AND now clause + claim + subject extraction
RAG_GRAPH_EXTRACT_MODEL=qwen/qwen3.8-27b   # party + affiliation extraction (its own env, not under RAG_MODEL_ALL)
RAG_INGEST_LIST_MODEL=off              # no cross-model list union (Qwen-vs-Qwen is a no-op anyway)
RAG_INGEST_CLAUSE_SAMPLES=4            # same-model 4-sample union for list recall (or pass samples=4 in code)
```

That's the whole set. With `RAG_MODEL_ALL=qwen/qwen3.8-27b`, you **no longer need to pass `extract_model=qwen`** to `aproduction_document_ingest` / the compliance / query entrypoints — the env covers them. (You still *may* pass it per-call to override for a specific stage; it wins.)

The one env that stays separate is `RAG_GRAPH_EXTRACT_MODEL` (party/affiliation) — set it explicitly as above.

## Reminders
- **`samples=4`** is the right same-model lever now that Qwen is primary: it recovers the *inconsistent* list under-enumeration that a cross-model partner would otherwise catch. Confirmed as the correct choice with `list_model=off`.
- **`RAG_SERVING=vllm`**: your self-hosted vLLM server's `--served-model-name` must equal `qwen/qwen3.8-27b` (the client requests the model by that string for every stage). On `RAG_SERVING=openrouter` it's the OpenRouter slug.
- Cross-check: with the config above, every ingest and query LLM surface resolves to Qwen — clause/claim/subject extraction, party+affiliation, function-classify, chunk boundary-refine, semantic judge, generation, and the compliance/relational judges.

Reference: commit `77b4acb`, `capabilities/dg_extraction.py::default_extraction_model` (RAG_MODEL_ALL-aware default), ADR-0097 (the caller-configurable model args), `models/profiles.py` (`_PRODUCT_LLM`, `RAG_MODEL_ALL`).
