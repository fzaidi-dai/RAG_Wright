# docling-graph extraction model A/B (GP-1B.4 / .4b / .4c) — 2026-07-28

Which model should power docling-graph party extraction (GP-1B). Metric: party recall/precision vs
name-filtered CUAD "Parties" gold + latency, on a 12-contract dev slice (`scripts/dg_model_ab.py`,
`score_parties`). Relative comparison on the same noisy gold + hard latency/runtime signals.

## Results (12-contract dev slice)

| Model | gen | recall | precision | lat/doc | runtime |
|---|---|---|---|---|---|
| deepseek-v4-pro (reference) | — | **0.829** | **0.889** | 41s | OpenRouter, paid |
| **ibm-granite/granite-4.1-8b** | 4.1 | **0.815** | 0.861 | 5.7s | OpenRouter (IBM-open) |
| granite4:tiny-h (7B MoE/1B act) | 4.0 | 0.773 | 0.875 | ~local | Ollama local |
| granite4:micro (3B dense) | 4.0 | 0.764 | 0.830 | ~local | Ollama local |
| granite4:small-h (32B MoE/9B act) | 4.0 | 0.745 | 0.819 | 13.2s | **Modal A10 (self-hosted)** |
| gemma-4-31b (GP-1B.4 only) | — | 0.648 | 0.599 | 25s | OpenRouter |

## Findings

- **`granite-4.1-8b` is the best Granite and ~= DeepSeek** (recall 0.815 vs 0.829; precision 0.861 vs 0.889)
  at **7x the speed** (5.7s vs 41s), and it is **IBM-open** (OpenRouter now; self-hostable). The Granite
  thesis is validated: a Granite model reaches DeepSeek-class contract extraction.
- **Generation beats size.** The 32B `granite4:small-h` (**4.0**) is the *weakest* Granite here (0.745/0.819) —
  it does NOT beat the 8B **4.1** or the smaller 4.0 variants. The 4.1 architecture matters more than raw size.
  So there is no reason to run the 32B.
- **Gemma underperformed** on extraction (0.648/0.599) and is out (extraction != the reasoning/judgment tasks
  where Gemma wins).
- Caveats: 12 contracts; CUAD "Parties" gold is noisy (role labels filtered heuristically); DeepSeek vs
  4.1-8b recall is within noise, though DeepSeek's precision edge is real.

## Winner (recommended)

**`ibm-granite/granite-4.1-8b`** for GP-1B.5 — best Granite, ~=DeepSeek quality, fastest, IBM-open. DeepSeek is
the marginal quality leader if precision is paramount.

## Modal readiness (GP-1B.4c)

**Modal Endpoints (the managed product) does NOT support Granite** — its catalog is architecture-limited
(Qwen / Gemma-4 / DeepSeek-V4 / GLM / Nemotron / gpt-oss / Kimi; no Granite, whose hybrid Mamba2/MoE isn't a
catalog base). Notably DeepSeek-V4-Pro and Gemma-4-31b ARE in the catalog. So Granite is self-hosted via a
custom **`@app.server()`** (Modal's new serverless-server primitive): `scripts/modal_granite_server.py` runs
Ollama on an **A10** GPU, model cached in a Modal **Volume** (`prepull` on CPU), `unauthenticated`, Ollama API
proxied at the server URL — our `ollama_model(base_url=<url>)` seam talks to it unchanged. Verified end-to-end
(32B loaded on the A10G, 200 responses). Reusable for any Ollama Granite; a persistent Modal-hosted Granite is
a `modal deploy` away (stop with `modal app stop rw-granite-ollama --yes`).

Gotchas recorded: A100 needs a payment method on this account (A10 works); Ollama's installer needs `zstd`; a
large pull can hit a transient digest mismatch (retry — Ollama resumes from cached blobs).
