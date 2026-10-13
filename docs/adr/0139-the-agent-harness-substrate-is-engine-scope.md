# ADR-0139: The agent harness substrate is engine scope

**Status:** accepted · **Date:** 2026-10-12 · **Supersedes in part:** ADR-0052 (the placement of the agent harness
in the product) · **Amends later:** ADR-0041 (an `agent` capability kind, PS-33) · **Related:** ADR-0128 (the public
tiers), ADR-0134 (skill placement), ADR-0015 (the RLM Deep Agent), RuleWright's request of 2026-10-12 and its
ADR-0021 amendment

## Context

ADR-0052 put the agent harness in the product: each product would hand-build its orchestration. RuleWright has
since built one on Deep Agents and LangGraph, and measured it: 32 modules, 7,514 lines. Only three import anything
from the product, and 21 name no domain concept. What it found expensive is domain-free: the loop, error levels and
deadlines, retry composition, human-in-the-loop and the interrupt protocol, replay on resume, chat interaction,
per-layer context management, memory practice (including cross-tenant namespaces), metering, bounded grading,
repeat suppression and the decision registry. Each of those cost it a real defect to get right. What makes the
product what it is lies elsewhere: its tools and the capabilities it gives each agent, the guardrails that read
content, its prompts and skills, its decision models.

Two more products on the engine are planned, and each needs a harness. Left in the product, the substrate would be
written three times and drift three ways. Since ADR-0052 the engine has also been de-domained into a substrate for
building domain products (ADR-0124, ADR-0128); driving its capabilities with agents is part of that job.

A second reason surfaced on review. `create_deep_agent(...)` returns a LangGraph `CompiledStateGraph`, the same type
as the engine's workflow subgraphs, and the capability invoker resolves any `impl_ref` factory without knowing what
is behind it (`capabilities/invoke.py`). So a harness, customised for one job, can itself be registered as a
capability: an agentic capability beside the deterministic functions, the models and the workflow subgraphs. The
RLM capabilities already are this, built by hand (ADR-0015). Only the engine owns the capability layer and ARD, so
only the engine can make that a supported pattern.

## Decision

1. **The agent harness substrate moves into the engine**, as a third public tier, `rag_wright.harness`, beside
   `rag_wright.api` (a product's seam) and `rag_wright.pack_sdk` (a pack's code).
2. **The substrate is what exists, separated; it is not redesigned for products not yet built.** It is RuleWright's
   working harness with its product parts removed. No protocol is added to serve a future product's needs; a
   product customises the substrate through what the agent framework already provides (middleware, tools, prompts,
   skills, subagents and plain functions), and a need it cannot meet that way is an engine request like any other.
3. **RuleWright does the separation**, because the code and the analysis are in its repo. It moves the substrate
   into a package that imports nothing from the product and names no domain concept, its product parts consume
   that package, and it hands the package over. The engine then adopts it (PS-32): the harness imports engine
   code only through `rag_wright.api`, no other engine module imports the harness (import contracts), the
   domain-vocabulary guard covers it, and its deployment-specific dependencies (the Postgres checkpointer) sit in an
   optional extra, `rag-wright[harness]`, so a plain engine install stays as it is.
4. **The product keeps** its tools and operations, its agents and their roster, prompts and skills, the guardrails
   that read content, its decision models, its tenancy model and its UI.
5. **The blueprint ships with the code.** RuleWright's `building-an-agent-harness` skill moves into
   `src/rag_wright/.agents/skills/` (ADR-0134), version-matched to the substrate it describes.
6. **Agentic capabilities are a later, separate workstream (PS-33).** It adds an `agent` capability kind (amending
   ADR-0041: invoked like a subgraph, but declared as non-deterministic, tool-using, possibly pausing for a human,
   and spending against a budget), typed contracts at the boundary, interrupts across the capability boundary,
   deadlines and budgets passed down nested agents, and a worked example on public data. Not started until agreed.

## Consequences

- One substrate, fixed once, for every product's chat agent and later for agentic capabilities.
- The engine's support surface and public API grow; a harness change ships as an engine release. The tier may
  change between minor releases until a second product uses it, and every change goes in that release's migration
  guide.
- `deepagents`, `langgraph` and `langchain-quickjs`, until now used only by the parked RLM agent, move onto a
  supported path. Their floors stay floors (ADR-0112). A product still declares its own floors for the agent
  framework it calls directly (RuleWright's ADR-0021, decision 1).
- Publishing the substrate in the open core cannot be undone. It is accepted because the substrate is not where a
  product's value lies; the product keeps its tools, guardrails, prompts, skills and decision models closed.

## Alternatives considered

- **Each product writes its own** (ADR-0052 as it stood): the same expensive code three times, the same defects
  rediscovered.
- **Wait for a second product's harness, then decide:** guarantees a second copy to merge later.
- **A separate open package, its own repo:** clean scope, but a second CI and release line and an engine-by-harness
  compatibility matrix, every seam change becoming two releases; and agentic capabilities would still need the
  capability layer, which is the engine's.
- **A private shared package:** right only if the harness were where a product's value lies; RuleWright's
  measurement says it is not.

## Correction, 2026-10-13

RuleWright re-measured its harness before starting the separation (its engine request
`2026-10-12-correction-to-the-harness-measurement.md`). The Context's figures were wrong: **13** modules import
something from the product (6 of them a domain package), not three; **16 to 19** name no domain concept once comments
and docstrings are stripped, not 21; **10 modules (1,092 lines) lift unchanged and 22 modules (6,422 lines) need
work** (32 modules, 7,514 lines), so the separation is staged work, not three imports turned into protocols. (These
three figures were first published here as 18, and as 12 / 2,193 lifting against 20 / 5,321; RuleWright's committed
measurement script corrected them the same day, see below.) A third axis, invisible to both of those
measurements, also keeps code product-shaped: deployment choice, vendor names and tenancy shape (a model id and an
`is_openrouter()` helper in code, a direct `psycopg` import, a `(customer, workspace)` memory namespace).

The decision is unchanged; its reasons did not rest on these figures. Two details of it are sharpened:

- **The substrate's seams are the agent framework's own types**, not new protocols: it accepts a LangGraph
  checkpointer, a memory namespace prefix from the caller, and a chat model (or a model profile name), and names no
  model, vendor, database driver or tenancy shape. The product builds the Postgres checkpointer and passes it in, so
  **the engine needs no `rag-wright[harness]` Postgres extra** (Decision 3's extra is withdrawn).
- **PS-32's acceptance gains two checks:** RuleWright's third guard (deployment, vendor and tenancy shape) runs over
  `rag_wright.harness` in the engine's suite, and the engine repeats all three measurements itself at handover.
  Building an agent's chat model from a model profile, with tracing and usage metering, goes on `rag_wright.api`
  first (PS-36), so the product's model code is deleted rather than moved.

### The measurement, as RuleWright's committed script reports it (2026-10-13)

- **The lift/work split counts all three axes.** The first 12 / 2,193 counted only the product-import and
  domain-vocabulary axes, so it credited `models.py` (a vendor named in code) and `checkpoints.py` (a `psycopg`
  import) as lifting unchanged. With the deployment, vendor and tenancy axis, 10 modules (1,092 lines) lift and 22
  (6,422 lines) need work: less lifts for free than first written.
- **The domain-vocabulary count is a band, 16 to 19.** The stem list is curated (14 stems), because a list derived
  from the ontology (173 tokens) holds generic English such as "change" and "available" and would wrongly mark
  generic code as domain-bound. Six stems (audit, document, matter, party, policy, requirement) are both a domain
  term and an ordinary word in a generic harness, so they are counted both ways.
- **The third guard is RuleWright's `tests/test_harness_genericity.py`**, which PS-32 runs over `rag_wright.harness`
  from adoption. It is a ratchet: the known violations may only shrink, a new violation fails, and a stale exemption
  fails, so a finished stage cannot go unrecorded. A re-implementation must read tokens, not lines: four tenancy hits
  (the `customer_id=…, workspace_id=…` pairs in `accounting.py`, `composition.py`, `runs.py` and `tools.py`) span
  source lines and are invisible to `grep`.
- **Progress since:** `models.py` went from 538 to 448 lines (PS-36 adopted: the default model id, `is_openrouter()`,
  both `extra_body` dialects, the provider pin and the profile registration deleted), and `checkpoints.py` left the
  harness as product deployment code, so the deployment axis is 0 and the harness is 31 modules.
