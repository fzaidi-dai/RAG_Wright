# ADR-0039: Self-hosted open-model stack on Modal/A100 is the product substrate (no foundation-model-API dependency)

Date: 2026-08-03
Status: Accepted

## Context

RAG_Wright's capabilities were built model-neutral by design: structured-output calls go through the
model-profile seam (assumption 2 / the model rule), never a hardcoded provider, and the two-halves boundary
keeps capabilities independent of any orchestrator or model host. We then measured the two facts that make an
open-model substrate viable (2026-08-03, memory `local-vs-hosted-granite-throughput`, ADR-0038 build):
- granite-4.1-8b (bf16) = **~17 GB VRAM** → fits an A100-40GB with headroom (measured).
- **vLLM-Granite on one A100 (33 req/s) BEATS the single OpenRouter provider (23.5 req/s)** for bulk extraction.

Product rationale (the decision driver): we do **not** want the product to depend on OpenRouter or any hosted
foundation model. Running open-source models on an A100 lets us (a) offer a **hosted** service (spin up Modal
A100 containers on demand, kept warm via Modal weight-caching + warm-pool tricks), and (b) ship a **dedicated
enterprise install** — our tested container on the customer's own A100 private cloud, so **contract data never
leaves their premises** (a real objection to foundation-model SaaS in legal). Cost is near-zero at our scale
(Modal's ~$30/mo credits ≈ 10–15 A100-40GB hours; the 506-contract bulk ingest is done, incremental ingests are
a few hours), which we pass to customers while keeping margins — a differentiator vs contract-management tools
built on foundation-model APIs.

## Decision

**The product runs on a self-hosted open-model stack on Modal/A100. OpenRouter is demoted to a dev/fallback path
behind the model-profile seam, not a production dependency.**

- **Models:** vLLM serving **granite-4.1-8b** (extraction + generation; structured output via vLLM guided
  decoding), **LegalBERT** (function classify) + **BGE-M3** (embed) on the same A100. Point the extraction /
  chunking / generation seam at the local vLLM OpenAI-compatible endpoint (a config change, not a rewrite).
- **Gemma:** attempt to **drop Gemma** by using granite-4.1-8b for the chunking (boundary-discovery) step too;
  if a distinct chunker model is needed, an unsloth-quantized Gemma-4 (q4) on the same box. (Two full models on
  one A100-40GB won't co-reside — 17 GB Granite + ~62 GB Gemma-4-31B — so this is an explicit test, not assumed.)
- **KG home:** the live KG (`ragwright_cuad_full`, ADR-0038) lives on a **Modal Volume**, served by an ArcadeDB
  container on Modal. This **supersedes ADR-0038's "adopt via remote-tunnel or restore-to-local"** — Modal is the
  home; local querying is a convenience that calls into the Modal KG + models.
- **Ingestion:** runs entirely on Modal/A100 (GPU classify/embed/extract co-located → localhost writes).
- **Query:** not real-time — a user tolerates a first-query warm-up. Structure (designed later,
  ENTERPRISE-CONTAINER): a continuously-running, **scale-to-zero** non-GPU Modal app holds the CPU pipeline
  cheaply; the GPU part (vLLM + classifier + BGE) is a **separate ephemeral A100 container** kept warm by the
  main app while requests arrive and torn down after idle (~5 min). Premium tiers = faster warm-up / longer
  keep-warm. Scale = pay Modal for more concurrent containers, adjust markup.

## Consequences

- **Cheap to adopt** precisely because of the model-profile seam + two-halves discipline — a retroactive payoff
  of those rules; the pivot is a seam config change, not a capability rewrite.
- **Differentiator:** data sovereignty (private-cloud install) + low marginal cost vs foundation-model SaaS,
  under a monthly subscription (run + support).
- **Must validate (turned into tasks):** (1) vLLM-Granite **structured-output** clause extraction matches
  OpenRouter *quality*, not just speed (MODAL-STACK-2) — the benchmark measured raw generation only; (2) Granite
  can do the **chunking** step well enough to drop Gemma (CHUNKER-OPEN); (3) **docling-graph's ~10 s/clause
  wrapper** becomes the dominant cost once the LLM is local+fast — trim it or accept it at 506-doc scale;
  (4) warm/cold **economics** of the query path (ENTERPRISE-CONTAINER).
- OpenRouter stays wired (the seam) for dev iteration + as a fallback, but is not required for the product.

See ADR-0038 (the KG + GCS backup), memory `gcp-bulk-ingestion-box` / `local-vs-hosted-granite-throughput`,
tasks MODAL-STACK-1/2, CHUNKER-OPEN, QUERY-SPECDEC, ENTERPRISE-CONTAINER.
