# RuleWright handoff: engine default is now `qwen3.8-27b-modal-or` (Qwen, no env needed)

Date: 2026-09-11 · on `origin/main` (commit `7ec5456`) · ADR-0100 (follow-up section) · **No caller API change. Exposed model arguments still win over everything.**

---

## What changed (one line)

Qwen 3.8-27B is now the engine's **out-of-the-box default** — with **no env set at all**. Previously "Qwen everywhere" required `RAG_MODEL_ALL=qwen/qwen3.8-27b`; now a fresh run with an empty environment already routes every LLM surface to Qwen via OpenRouter.

## The three defaults that flipped

All three built-in default constants moved from the un-pinned granite slug to the **backend-pinned** string `qwen3.8-27b-modal-or`:

| Surface | Constant | Covers |
|---|---|---|
| Every `model_for` role | `_PRODUCT_LLM` (`models/profiles.py`) | judge, generation, function-classify, chunk boundary-refine, synthesis |
| Ingest/query extraction | `_PRODUCT_EXTRACT_DEFAULT` (`capabilities/dg_extraction.py`) | clause-property, claim, subject extraction |
| Party/affiliation | `DEFAULT_GRAPH_EXTRACT_MODEL` fallback (`capabilities/graph_extraction.py`) | party + affiliation extraction |

With no env, all three resolve to `qwen3.8-27b-modal-or` → OpenRouter, served id `qwen/qwen3.8-27b`.

## What the string means (read this — it's a deliberate placeholder)

`qwen3.8-27b-modal-or` is **one `PROFILES` entry**: `backend=openrouter`, `served_model_id=qwen/qwen3.8-27b`, throughput provider routing, reasoning split by call class (on for the structured judge, off for free-text extraction).

The `-modal-or` name is intentional: the model is **destined for** your self-hosted Modal/vLLM substrate (ADR-0039), but Modal cold-start/warmup is **unsolved** (the snapshot path failed — GPU is not visible to vLLM during the CPU-snapshot enter; keep-warm is the only current answer). So the string is **routed via OpenRouter for now**. When warmup is solved, **we flip that one profile entry's `backend` to `vllm`** (+ `served_model_id=Qwen/Qwen3.8-27B` + the vLLM `chat_template_kwargs` reasoning flags) and every default follows — **no call-site change, and no change on your side** if you're on the default.

## What this means for your config

- **You can drop `RAG_MODEL_ALL=qwen/qwen3.8-27b`** if all you wanted was "Qwen everywhere" — the default already is Qwen. (Keeping it set is harmless.)
- **Your exposed model arguments are unaffected.** An explicit `extract_model` / `graph_extract_model` / `judge_model` / etc. passed to any entrypoint still wins over the default.
- **`samples=4`** stays the right same-model list-recall lever (Qwen is primary, so a cross-model list union is a no-op). Unchanged.

## One behavior change to be aware of: `RAG_SERVING` and the default

Because the new default is a **backend-pinned** string, it **no longer honors `RAG_SERVING`**. `RAG_SERVING` (`openrouter`|`vllm`) now only affects **un-pinned** strings (every plain slug like `qwen/qwen3.8-27b`, `ibm-granite/granite-4.2-8b` — those still fall back to it exactly as before).

So: setting `RAG_SERVING=vllm` alone will **not** move the default off OpenRouter. To point the default at your own server today, use a **pinned** string instead:

```
RAG_MODEL_ALL=qwen3.8-27b-modal          # pinned to vLLM (VLLM_BASE_URL), served-name Qwen/Qwen3.8-27B
VLLM_BASE_URL=https://<your-qwen-app>.modal.run/v1
VLLM_API_KEY=<your key>
```

That's the ADR-0100 mechanic: a string carries its own access. `-modal` = your server, `-modal-or` = OpenRouter, plain slug = follows `RAG_SERVING`. Mixing per stage is just choosing different strings on `extract_model` / `judge_model` / etc.

## When you stand up a warm Modal Qwen server

Tell us and we flip `qwen3.8-27b-modal-or`'s `backend` to `vllm` in the profile (one entry, one ADR line). Every default then routes to your server with the correct served-name, and **no config or code changes on your side** — the string vocabulary is stable.

Reference: commit `7ec5456`, ADR-0100 (follow-up section "the engine-wide default is now a pinned string"), `models/profiles.py` (`_PRODUCT_LLM`, the `qwen3.8-27b-modal-or` / `-modal` / `-or` entries), `models/seam.py::resolve_connection`, `capabilities/dg_extraction.py::default_extraction_model`, `capabilities/graph_extraction.py`. Full suite: 1546 passed.
