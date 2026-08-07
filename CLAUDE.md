# CLAUDE.md: RAG_Wright

Project memory and working rules for Claude Code. Read this in full at the start of every session. Fill in the Project parameters below once per engagement; the rest of the file then applies unchanged. These parameters are the same set used by the DreamAI Claude Code Build Playbook template, so fill them once and reuse across both.

> Conventions: acronyms expanded on first use, no em dashes, plain phrasing.

---

## 0. Project parameters

| Parameter | Value for this engagement | What it controls |
|---|---|---|
| Project name | RAG_Wright | The title |
| Spec | `RAG_Capability_Spec.md`, v0.1 | The single source of truth. This repo builds the capability half only; the Orchestration Spec's graph briefs are dispatched to the GraphWright compiler in a separate step and are not built here. |
| Stack | Python with uv, pytest, and Pydantic | The environment rule, test runner, and contract tooling |
| Index tool | Graphify | The code-and-docs index the agent must consult |
| Core libs | `docling`, `docling-core`, the ArcadeDB Python client, `FlagEmbedding` (BGE-M3 and BGE-reranker) or the chosen embedding and reranker clients, the OpenRouter client (an OpenAI-compatible client or the LangChain OpenRouter chat model), the MCP server framework, and the NER/OpenIE extraction libraries | The framework graph and the grounding rule |
| Arch rules | See "Standing architecture rules" below, each with its spec reference. | The standing architecture rules |
| Model access | Served through OpenRouter by default (a Gemma 4 class model for reasoning, generation, vision-to-text, and RLM; a smaller model for chunking and summarization; a larger model for quality-sensitive extraction), with a local open-model deployment as a supported mode. Structured-output calls go through the model-profile seam, never a hardcoded provider flag (assumption 2, section 4). | The model rule |
| Spec coords | Project structure in section 10 (finalized at Phase 0); requirements numbered FR-S (shared store and identifiers), FR-C (capability catalog), FR-I (ingestion-side), FR-Q (query-side). | How tasks and steps cite the spec |
| Data artifacts | The ArcadeDB data directory, MinIO or object-store artifacts, the parsed-document cache, embedding caches, the golden evaluation set outputs, and any model caches | The gitignore |
| Contract use | The Pydantic contracts (the ontology, the extraction contracts, the shared identifiers `chunk_id` and `entity_id`) double as the registered capability contracts each capability is registered under. | The contracts phase |

### The two-halves boundary (read this first)

RAG_Wright is the capability half of a GraphWright application. This repo builds the capabilities in the capability spec (parsing, chunking, embedding, hybrid search, reranking, graph extraction, entity resolution, ontology derivation, reasoning and generation, and the RLM skill) as ordinary, tested software, and registers each one under its FR-C name. It does not build the ingestion and query graphs; those are compiled from the companion Orchestration Spec by the GraphWright compiler in a later step, and they bind the capabilities this repo registers. So there is no graph, orchestration, node, or edge to build here. If a task looks like "wire the ingestion pipeline into a graph," stop and flag it: that is compiler work, not this repo's work.

---

## Environment: Python with uv, pytest, and Pydantic only. Never the wrong tool.

This is the most important operational rule in this file, because getting it wrong has cost real time before. The default DreamAI stack is Python with uv: a `.venv` defined by `pyproject.toml` and `uv.lock`. It is not a system python or pip setup. (For a non-default `Python with uv, pytest, and Pydantic`, replace the uv commands below with that stack's equivalent, but keep the single-environment discipline and the do-not-panic rule.)

- Run everything through uv. To execute any code, for any reason (running, testing, debugging, a quick check, a one-off script, a REPL), use `uv run`: `uv run python ...`, `uv run pytest`, `uv run ruff ...`, `uv run python -m ...`. Never call `python`, `python3`, or a script directly.
- Install and manage dependencies only with uv: `uv add`, `uv sync`, `uv remove`. Never run `pip`, `pip3`, or `pip install`, not even once, not even to "just quickly check something".
- Never write or suggest a bare `python` or `pip` command anywhere: not in a test step, not in a runbook, not in an instruction to us. Every command you produce that touches code or packages starts with `uv` or `uv run`.

The failure mode to avoid, which has happened before: a command fails with "module not found", "command not found", or a missing-environment error, and the agent concludes the environment is broken, then panics and starts suggesting that all libraries be reinstalled or the environment be recreated. Do not do this. That error almost always means one thing: you forgot the `uv run` prefix, or a dependency is not declared. The fix is to re-run with `uv run`, or to `uv add` the missing dependency. The environment is fine. Do not propose reinstalling libraries, recreating the environment, or switching to pip. If after using `uv run` and `uv add` a problem genuinely remains, stop and ask us.

## Source of truth

- `RAG_Capability_Spec.md` is the single source of truth for what to build and why. Do not invent requirements. If something is unspecified or ambiguous, ask, do not assume.
- The build playbook describes the build approach (skills, phases, indexing). Follow it.
- This file governs how you work with us. The human-in-the-loop rules below are not optional.

## Start of every session

Before doing anything else:

1. Read `RAG_Capability_Spec.md`.
2. Read `tasks.md` for the current task ledger. This is our shared memory of progress across sessions.
3. Read `docs/adr/` (the Architecture Decision Records) and honor every prior decision.
4. Confirm the Graphify graphs are available (the `framework` graph, covering `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries`, and the `project` graph), and that the LangChain docs MCP server is connected.
5. Tell us, in two or three lines, where we are: the last approved task, the next task, and any open question. Then wait for us before starting work.

## The working loop: one task, then stop for us

This is the most important workflow rule in this file. Work one task at a time and keep us in the loop after every task. Never run ahead.

For each task:

1. State which task you are about to do, by its id and the requirement and acceptance criterion it implements (using the scheme in `project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q`, for example "Task 7, implements FR-5.2, verified by AC-5").
2. Ground the library calls: query the `framework` graph for the exact symbols and signatures you will use, and show us what you found. Do not call an API you have not confirmed exists in the index. Never guess an API.
3. Define or confirm the contract for the task.
4. Write the failing test or tests against that contract (test-driven development, red).
5. Implement the minimum needed to pass (green). This task only. Do not touch other tasks or unrelated files.
6. Run the task's tests and its verify step. Show us the results and the diff.
7. Stop and wait for our explicit approval. Do not mark the task done. Do not start the next task.
8. Only after we approve: mark the task done in `tasks.md`, write an ADR if you made an architectural decision, update any affected docs, and commit all of it in one commit (see Version control). Then stop again and ask before starting the next task.

Hard rules about the gate:

- Never mark a task complete without our explicit approval.
- Never batch tasks. One task, then stop.
- If a task is finished and we have not responded, wait. Do not proceed on your own.
- If our review asks for changes, make them on the same task and return to step 6. Do not advance.

## How to write the code

These reduce rework and keep diffs clean. They bias toward caution over speed; use judgment on trivial steps.

- Think before coding. State your assumptions. If a requirement has more than one reading, present the options rather than picking one silently. If a simpler approach exists, say so and push back. If something is unclear, stop, name what is confusing, and ask.
- Keep it simple. Write the minimum code that satisfies the task and the spec, nothing speculative. No abstractions for single-use code, no configurability that was not asked for, no handling for scenarios the spec does not call for. The spec's required error handling stays; do not add beyond it. If you wrote 200 lines and 50 would do, rewrite it. Ask whether a senior engineer would call it overcomplicated.
- Change surgically. Touch only what the task requires. Do not improve, reformat, or refactor adjacent code that is not part of the task. Match the existing style even if you would do it differently. Remove only the imports or names your own change orphaned; if you spot unrelated dead code, mention it rather than delete it. Every changed line should trace to the task.
- Parallelize LLM calls in evals, tests, scripts, and agentic workflows. Whenever such a loop sends more than one model/LLM call, drive it concurrently with the async + semaphore pattern in the outer loop (`asyncio.Semaphore(N)` for backpressure + `asyncio.to_thread(...)` + `asyncio.gather(...)`, as in `embed_chunks` and `_summarize_all`), never sequentially. LLM calls are network-bound, so this is the same tokens and the same cost but far less wall-clock (sequential loops cost real dead time, e.g. the hour-long re-ingest). `gather` preserves order, so determinism holds; bound N against provider rate limits.

## Long-running background work: stream X/N progress and monitor it (never launch-and-forget)

This is the rule we keep having to repeat, now standing and non-optional. Any task run in the background that can take more than ~30 seconds (a corpus ingest, an eval, model training, a bulk LLM loop, a migration, a framework rebuild) MUST do BOTH of these, every single time:

1. **Emit periodic `X/N` progress to its log/stdout.** A start line stating the total `N`, then `[stage] i/N <what>` (flushed) at a sane cadence (per item or small batch), then an end summary. If the script or function you are about to run does not already stream `X/N` progress, ADD it before running (e.g. a `log(f"[ingest] {i}/{n} {doc_id}")` in the loop). A run whose only output is at the very end is not acceptable, because it is unmonitorable.
2. **Actively monitor it and report to us in `X/N` form.** Poll the log at sensible intervals (a Monitor until-loop, or periodic reads), and tell us where it is (`ingest 4/12, ~2m elapsed`). Never launch a background job and then sit silently waiting for the exit notification. If it stalls or errors, surface it with the offending log line.

This generalizes ADR-0030's training-run rule (progress + loss + checkpoints, always-monitored) to ALL long-running work. It is the fix for the repeated "you launched a background job with no visible progress" failure. When in doubt, over-report.

## Cross-session memory and decisions

State lives in committed files so any session can resume cleanly.

- `tasks.md` is the persistent task ledger. Each task has an id, the requirement and acceptance criterion it implements, a status (`todo`, `in-progress`, `awaiting-approval`, `done`), the files it touches, its verify step, and a one-line note. Keep a short "last approved / next up" summary at the top. Update a status only as the loop above allows, and the `done` status only after our approval.
- `docs/adr/` holds the Architecture Decision Records, one short file each: context, decision, consequences, a few lines each. Write one whenever you make an architectural decision. Give each an id.
- Recall decisions during work. When a task is shaped by a past decision, name the relevant ADR id and follow it. If a task would contradict an existing ADR, stop and raise it with us rather than quietly diverging.

## Version control

Commits are how the memory above becomes durable. Git history is the parallel record of what actually landed, and committed files are the source of truth when a new session resumes.

- Before starting a task, make sure the working tree is clean and committed, so a failed attempt can be discarded with git and you have a known-good point to resume from.
- After we approve a task, commit once, atomically: the code, the updated `tasks.md` status, any new ADR, and updated docs together. Name the task and its requirement and acceptance criterion in the message, for example "Task 7 (FR-5.2, AC-5): deterministic comparison node".
- Whenever you record to memory (a `tasks.md` status change or a new ADR), commit it. Memory and git stay in step.
- Every commit on the main line is a green, reviewed state: tests passing and the task approved. Never commit broken or unreviewed code as a save point on the main line; use a scratch branch for a mid-task checkpoint.
- Never commit secrets or generated data: `.env`, `the ArcadeDB data directory, MinIO/object-store artifacts, parsed-document and embedding caches, evaluation outputs, and model caches`, the environment, and caches stay gitignored.
- `tasks.md` is the authoritative status ledger; git history is the record of what landed. If they ever disagree, stop and raise it with us.
- Commits are local. Ask before pushing to a shared remote.

## Standing architecture rules (from `RAG_Capability_Spec.md`)

- One store: ArcadeDB holds both the hybrid retrieval index and the knowledge graph; there is no cross-store join to keep consistent (FR-S.1, tech stack). LanceDB is not a default; it is only the eval-gated Phase 1 fallback for the retrieval leg, behind the query-skill seam (FR-S.5, Phase 1).
- Identifiers are load-bearing and fixed before building: `chunk_id` is source-document id plus chunk index plus content hash; `entity_id` is the canonical registry id. Changing either scheme breaks the link between chunks and graph nodes, so it is an ask-first change (FR-S.2, FR-S.3).
- Provenance and confidence on everything: source document and chunk for text; `EXTRACTED`, `INFERRED`, or `AMBIGUOUS` on graph-derived facts (FR-S.4). No claim without a citation (FR-Q.6).
- Deterministic capabilities are testably deterministic: the RLM chunking capability uses temperature zero or structured output, boundary validation, and a content-hash gate; expensive ingestion stages are content-hash gated (FR-I.1, FR-I.5).
- The RLM skill (FR-C.10) is authored skill content (a SKILL.md), built as ordinary software, and used by the RLM chunking and RLM synthesis capabilities. It is not provided by any build tool and is not a compiler feature in this repo.
- Model-neutral through OpenRouter by default, local open-model deployment supported; structured-output calls go through the model-profile seam, never a hardcoded provider or model flag in code (assumption 2, tech stack).
- The graph is the relationship layer only; heavy structured data is not put in the graph, and no specific external analytics system is assumed to exist inside this component (section 8, boundaries).
- Register each built capability under its FR-C name; the capability contract is what it registers (contract use).
- Model access: `OpenRouter by default (Gemma 4 class for reasoning/generation/vision/RLM; smaller model for chunking/summarization; larger model for quality-sensitive extraction), local open-model deployment supported; structured-output calls go through the model-profile seam`.
- Generative AI only where the spec allows; mechanical work that must be exact stays deterministic and is never hardcoded where the spec says it must be computed.
- Structured output on open models: many open models (the Qwen and DeepSeek ladder among them) reject a forced tool or schema choice while in thinking or reasoning mode, so structured output is configured through the model-profile seam keyed by model id, applied at the single point where a model is constructed, never by assuming a model supports a forced structured call. The profile carries the structured-output method (default `function_calling`, more broadly supported than `json_schema`) and an optional structured-only extra body that disables thinking on the forced structured call only, leaving free-text and reasoning calls unaffected. Provider flags are empirical and live in the profile config and a dated ADR, never in node or agent code. If a node genuinely needs to reason and then emit a contract as two separable, individually checkable steps, split it into a reasoning node and a structured-emit node instead, and prefer that split at high control for auditability; otherwise use the profile.
- Secrets only in `.env`, never committed.
- Library grounding: before writing or changing any code that calls a library in `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries` (or any indexed library), query the `framework` graph for the exact symbol and signature first. Two tools, two jobs: the code graph (`Graphify`, structure from source) is the source of truth for what exists and its exact signature, and is what grounding resolves against; the LangChain docs Model Context Protocol (MCP) server (hosted, current) is for understanding concepts and how a feature is meant to be wired. The ordering on any unfamiliar surface is docs first to understand, code to confirm, then write the call. Never let a method name seen in prose or an example stand in for confirmation against the code graph; a signature in docs is not proof the installed version has it. When a framework surface has **no source-tree coverage and no MCP** (for example the ArcadeDB SQL vector functions `vector.fuse` / `vector.sparseNeighbors` / the `LSM_*_VECTOR` DDL, which the `arcadedb-python` client does not wrap), ground it by LLM-extracting its relevant vendor docs into the **same single framework index** alongside the Python AST, per ADR-0008: a tight committed corpus under `docs/vendor/`, a cost-once extraction via OpenRouter/DeepSeek V4 Pro (`scripts/extract_arcadedb_docs.py`), and a free additive merge on every rebuild (`scripts/merge_docs_into_framework.py`, run by `refresh_framework_graph.sh`). One index, one authority; do not reverse-engineer a server binary.
- Onboarding a new library into the grounding index (when the capabilities start calling a library that is not grounded yet): first `uv add` it (declare and install), then get its Application Programming Interface (API) into the framework index by one of three routes, then rebuild with `refresh_framework_graph.sh` and query the graph before writing the call. Pick the route by the library's shape. (1) Pip-installed and stable: add the package to the `PKGS` list in `refresh_framework_graph.sh`, so it is indexed from site-packages and version-matched by construction. (2) Not pip-installable on this host, or fast-moving, or a monorepo where the layout matters: clone-stage it the way `vllm` and `fastmcp` are, that is `graphify clone <github-url>` then a staging block in `refresh_framework_graph.sh` that rsyncs the core package into the stage; refresh the clone with `git pull` and keep it at the tag matching the installed version. (3) No source tree and no docs Model Context Protocol (MCP): LLM-extract the vendor docs into the same index per ADR-0008. Two rules hold across all three routes. Ground against BOTH the code graph AND the installed `inspect.signature`, because a clone at latest can drift from the pinned install on a fast-moving library. And watch transitive dependencies: a new library can pull in a package that flips the behavior of code that gates on whether something is importable (adding `fastmcp` pulled in `opentelemetry-api` and activated the observability seam; the fix was to gate activation on a real provider being installed, not on mere importability). One index, one authority.

## Skills to use

- `planning-and-task-breakdown` for the Plan and Tasks phases, read-only Plan Mode first.
- `test-driven-development`, used as contract-first TDD: contract, then failing test, then implement.
- `incremental-implementation`, one task at a time.
- `context-engineering`, load only the relevant spec section and the grounded API per task, not the whole spec.
- `documentation-and-adrs`, ADRs kept short.
- `debugging-and-error-recovery`, on demand only when a task is stuck.
- `using-agent-skills`, to discover and invoke the above.
- `Graphify`, for the `framework` and `project` graphs.
- The LangChain docs MCP server (`https://docs.langchain.com/mcp`), for understanding LangGraph and Deep Agents concepts. It explains; it does not confirm. Grounding still resolves against the code graph.

## Phases (each ends with our review gate)

- Phase 0, setup: initialize git with a `.gitignore` covering `.env`, `the ArcadeDB data directory, MinIO/object-store artifacts, parsed-document and embedding caches, evaluation outputs, and model caches`, the environment, and caches; create the environment and declare dependencies per `Python with uv, pytest, and Pydantic`; scaffold the folder hierarchy from the structure section named in `project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q`; build the `framework` graph from `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries` and the `project` graph; make the first commit. Do not implement yet.
- Phase 1, Plan: produce `plan.md` in read-only Plan Mode. We review.
- Phase 2, Tasks: produce `tasks.md`, each task mapped to its requirement and acceptance criterion, with acceptance, verify step, and files. We review.
- Phase 3, Contracts: define the contracts in `src/contracts/` (Pydantic on the default stack). These also serve as `the registered capability contracts each capability is registered under`. We review.
- Phase 4, Build: the working loop above, per task, each with our approval gate.
- Phase 5, Integrate and end to end: wire the components as the spec defines, run the end-to-end scenario or scenarios and any dry run, confirm the acceptance criteria. We review.

## Boundaries

- Always: ground library calls before writing them; run the task's tests before presenting it; keep deterministic work deterministic; keep secrets in `.env`; use the project stack (`Python with uv, pytest, and Pydantic`) for everything; stream `X/N` progress from every long-running background task and actively monitor it, reporting progress to us (never launch-and-forget); stop for our approval after each task; commit each approved task, starting from a clean working tree.
- Ask first: changing `RAG_Capability_Spec.md`, adding a dependency, changing the data model or schema, switching models, or attempting any Phase 4 case-by-case component (multi-vector, typed-functional RLM, multimodal embedder, canonical skeleton).
- Never: mark a task done without our approval; start a new task without our approval; launch a long-running background task without `X/N` progress logging and active monitoring (launch-and-forget); on the default stack, run a bare `python`, `python3`, `pip`, or `pip install` command (always `uv run` / `uv add`); declare the environment broken or suggest reinstalling libraries or recreating the env when a command fails (re-run with `uv run` or `uv add` instead); hardcode a provider-specific or model-specific flag (for example a reasoning-disable flag) in node or agent code, or assume an open model supports a forced structured-output choice in thinking mode (use the model-profile seam); commit secrets; violate any standing architecture rule (for example changing an identifier scheme, letting a claim out without a citation, or putting analytical data in the graph).

---

## How to reuse

1. Copy this file to `CLAUDE.md` in the new repo, fill in the Project parameters table, and delete the worked example if you like.
2. This file is already filled for RAG_Wright; no placeholders remain.
3. Adjust the Environment section and the stack slots only if the project is not the default Python with uv. Everything else, the human-in-the-loop gate, the session-start routine, the memory and commit rules, the code-quality rules, the skills, the phases, and the boundaries, stays as written.
