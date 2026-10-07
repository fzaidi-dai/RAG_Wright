# DreamAI Claude Code Build Playbook: RAG_Wright

The DreamAI recipe for building a spec-driven project with Claude Code, following spec-driven development, test-driven development, contracts-first, and library-grounded coding. Filled for RAG_Wright: the parameters below are set, and the body applies as written. RAG_Wright is the reusable **engine/platform** (open-core candidate) of the Hybrid RAG system; the user-facing **product** is a separate repo that depends on it, and GraphWright (the orchestration compiler) is PARKED (ADR-0052).

> **Current active workstream (2026-10-07): ingestion hooks** (ADR-0124): a generic, domain-neutral ingestion
> builder (`build_ingestion`) with domain hooks, a neutral default schema, and the reference pack gathered into its
> own packages (`rag_wright.packs.contracts` / `rag_wright.packs.compliance`, ING-8). Still open: ING-3b (the legal
> grouping patterns into the contract `.ttl`), ING-5 (the documentation audit) and ING-CLEAN (scratch databases).
> Driven by **`docs/specs/ingestion-hooks/plan.md`** (`ING-*`); follow that plan for current work. The preceding
> **engine-prep** workstream (`docs/specs/engine-prep/plan.md`, `PREP-*`) is closed: it packaged and documented the
> engine. The **engine-platform boundary** (engine API + capability runtime + de-domaining, ADR-0117) landed the
> public API; its spec/ledger (`docs/specs/engine-platform/SPEC.md` + `TASKS.md`) remain the reference for that
> surface. The consolidated doc set is under `docs/` (`concepts`, `architecture`, `installation`, `configuration`,
> `quickstart`, `reference-pack`, `api/`, and the `domain-adaptation/` guide); historical docs are in
> `docs/archive/`; the ADR index is `docs/adr/README.md`; product-starter templates are
> `docs/templates/product-starter/`.

> Conventions: acronyms expanded on first use, no em dashes, plain phrasing.

---

## 0. Project parameters (fill in per engagement)

| Parameter | Value for this engagement | What it controls |
|---|---|---|
| `RAG_Wright` | | The title and the commit scope |
| ``RAG_Capability_Spec.md`` | path and version, for example `SPEC.md`, v1.0 | The single source of truth |
| `MVP` | demo, MVP, or production | How lean to keep the process (section 7) |
| `Python with uv, pytest, and Pydantic` | default: Python with uv, pytest, and Pydantic | Environment, test runner, and contract tooling. Override for non-Python projects |
| `Graphify` | default: Graphify | The code-and-docs index the agent must consult |
| `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries` | the fast-moving libraries the agent must not hallucinate | The framework graph contents. Leave empty if nothing needs indexing |
| Arch rules | the spec's always-in-force constraints, each with its spec reference | The architecture rules in CLAUDE.md (section 3) |
| `One product LLM for every role, including vision OCR (Qwen3.8-27B, via OpenRouter by default or self-hosted vLLM, selected through the model profile), plus the Jev typed-decision model; structured-output via the model-profile seam` | the model or model ladder and gateway, if any | The model rule in CLAUDE.md |
| `project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q` | where the folder-structure section is, and the requirement scheme (for example FR, AC, NFR) | So steps and prompts cite the spec correctly |
| `the ArcadeDB data directory, MinIO/object-store artifacts, parsed-document and embedding caches, evaluation outputs, and model caches` | generated files to ignore beyond `.env` and the environment | The `.gitignore` (section 2) |
| `the registered capability contracts each capability is registered under` | what the contracts also serve as downstream, if anything | The contracts phase (section 4) |

### Filled values for RAG_Wright

- Project name: RAG_Wright.
- Spec: `RAG_Capability_Spec.md`, v0.1. This repo is the ENGINE/platform (the FR-C / FR-I / FR-Q capabilities + ingestion/query pipelines + MCP + ARD, as ordinary tested software). The user-facing PRODUCT is a separate repo depending on this engine; GraphWright (the orchestration compiler) is PARKED (ADR-0052).
- Build stage: MVP.
- Stack: Python with uv, pytest, Pydantic (the default).
- Index tool: Graphify.
- Core libs: `docling`, the ArcadeDB Python client, the BGE-M3 and BGE-reranker clients, the OpenRouter client, the MCP server framework, and the NER/OpenIE extraction libraries, indexed as one framework graph.
- Arch rules: one ArcadeDB store for both index and graph (FR-S.1); identifier schemes fixed before building (FR-S.2, FR-S.3); provenance and confidence on everything, no claim without a citation (FR-S.4, FR-Q.6); deterministic capabilities testably deterministic and content-hash gated (FR-I.1, FR-I.5); the RLM skill is authored SKILL.md content, not a build-tool feature (FR-C.10); model-neutral via OpenRouter with structured output through the model-profile seam (tech stack); the graph is the relationship layer only (section 8); register each capability under its FR-C name.
- Model access: one product LLM for every role, including reasoning, generation, vision-to-text, RLM and vision OCR (Qwen3.8-27B, profile `qwen3.8-27b-modal-or`, served through OpenRouter by default; self-hosted vLLM supported), chosen through the model profile and overridable per role; plus the Jev typed-decision model (`jev_decision`, ADR-0119) for calibrated closed-set decisions; structured-output calls through the model-profile seam.
- Spec coords: project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q.
- Data artifacts: the ArcadeDB data directory, MinIO or object-store artifacts, parsed-document and embedding caches, evaluation outputs, and model caches.
- Contract use: the Pydantic contracts (ontology, extraction contracts, shared identifiers) are the capability contracts each capability is registered under.

### The engine / product boundary (ADR-0052)

This repo is the ENGINE/platform (open-core candidate): it builds and registers the capabilities from the capability spec (FR-C, FR-I, FR-Q) plus the ingestion/query pipelines, MCP servers, and ARD, as ordinary tested software. The user-facing PRODUCT (contract management + compliance app: UI, product-named tools, hand-built orchestration, connectors, guardrails, feedback loops) is a SEPARATE, closed-source repo that depends on this engine and has its own SPEC/plan/tasks; product work is not done here. Dependency direction is strict: Product → Engine, never the reverse. GraphWright (the orchestration compiler) is PARKED (ADR-0052) — no compiler step; the product hand-builds orchestration, which becomes GraphWright's future spec. If a task is UI, a product-named tool, or higher-level orchestration, it belongs in the product repo; flag it.

---

## 1. The skill set to install

| Skill | Source | Role | Handling |
|---|---|---|---|
| spec-driven-development | Addy Osmani | Umbrella process. Past the Specify phase once the spec exists. | Keep |
| planning-and-task-breakdown | Addy Osmani | The Plan and Tasks phases, read-only Plan Mode first. | Keep as-is |
| incremental-implementation | Addy Osmani | Implement one task at a time. Referenced by the spec skill. | Keep |
| test-driven-development | Addy Osmani | Red, green, refactor per task. | Keep, customized into contract-first TDD (section 4) |
| context-engineering | Addy Osmani | Load only the relevant spec section and the right API per task. | Keep, central to indexing |
| documentation-and-adrs | Addy Osmani | Running docs and Architecture Decision Records (ADRs). | Keep, ADRs kept short |
| debugging-and-error-recovery | Addy Osmani | Systematic debugging. | Keep, on demand only |
| using-agent-skills | Addy Osmani | Meta-skill so Claude Code discovers and invokes the rest. | Keep |
| library-grounding | Custom | Mandatory: query the index for the exact API before using a library. | Add (section 5) |
| authoring-a-capability | Custom (`.claude/skills/`) | How to author a new engine capability of any kind (subgraph/function/model/agent_skill/mcp_tool): the shared registration + ARD + invocation contract, per-kind specifics, and the conformance guardrail. | Add (ADR-0117) |
| creating-evals | Custom (`.claude/skills/`) | Write a capability's eval FIRST (eval-first/TDD): gold-set design, per-capability-kind metrics, gate-vs-diagnostic. | Add |
| classifier-opportunity-analysis | Custom (`.claude/skills/`) | Find where an LLM call can become a deterministic rule / trained classifier / routing decision (upstream of setfit). | Add |
| setfit / laya | Custom (`.claude/skills/`) | Build a trained classifier (SetFit) or fine-tune the open System-1 decision model (Laya); A/B the managed Jev decision model (ADR-0119). | Add |
| qwen-vllm-modal | Custom (`.claude/skills/`) | The self-hosted Qwen3 vLLM substrate on Modal for bulk teacher-labeling / production inference (ADR-0110). | Add |
| using-the-rag-wright-engine | Custom (`.claude/skills/`) | The CONSUMER-side playbook for a product repo building on the engine — kept here, version-matched; the product links it in. | Add |
| `Graphify` | for example safishamsi/graphify | Builds the queryable knowledge graphs the agent consults. | Install |

Contracts-first is folded into the TDD skill rather than added as a separate skill, to keep the active set small.

---

## 2. One-time setup (Phase 0)

1. Place the spec at ``RAG_Capability_Spec.md`` in the repo root (or `docs/`).
2. Initialize git with a `.gitignore` covering `.env`, the environment, caches, and `the ArcadeDB data directory, MinIO/object-store artifacts, parsed-document and embedding caches, evaluation outputs, and model caches`.
3. Create the environment and declare dependencies per `Python with uv, pytest, and Pydantic`. For the default Python stack: `uv venv`, then `uv add` (not requirements.txt), and use uv for everything from here on (`uv add`, `uv sync`, `uv run`); commit `uv.lock`.
4. Create `CLAUDE.md` with the standing rules in section 3, with the standing architecture rules and `One product LLM for every role, including vision OCR (Qwen3.8-27B, via OpenRouter by default or self-hosted vLLM, selected through the model profile), plus the Jev typed-decision model; structured-output via the model-profile seam` filled in.
5. Install the skills above into the Claude Code skills directory.
6. Install `Graphify` and build two separate graphs:
   - `framework` graph: point it at the installed source and docs for `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries`, scoped to the modules actually used, not whole monorepos. Index them as one graph so cross-package relationships stay traversable. Skip this graph if `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries` is empty.
   - `project` graph: point it at the repo root.
   - Wire incremental updates and the SessionStart hook so graph status loads at the start of every session.
7. Connect the LangChain docs MCP server through Claude Code's MCP mechanism: `claude mcp add --transport http docs-langchain https://docs.langchain.com/mcp` (add `--scope user` for all projects). This is a connected tool, not a built index, so there is nothing to construct or refresh. It is for understanding library concepts; grounding still resolves against the code graph.
8. Ask Claude Code to scaffold the folder hierarchy from the structure section named in `project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q`, then make the first commit.

---

## 3. CLAUDE.md standing rules

```
- `RAG_Capability_Spec.md` is the single source of truth. Do not invent requirements. If something is
  unspecified, ask, do not assume.
- Architecture rules from the spec, always in force:
  the standing architecture rules (see CLAUDE.md section 3 / 'Standing architecture rules')
- Model access: One product LLM for every role, including vision OCR (Qwen3.8-27B, via OpenRouter by default or self-hosted vLLM, selected through the model profile), plus the Jev typed-decision model; structured-output via the model-profile seam
- Use the project stack consistently (Python with uv, pytest, and Pydantic). For the default Python stack: use uv,
  not pip, for environment, dependencies, and commands (uv venv, uv add, uv sync, uv run);
  dependencies in pyproject.toml; commit uv.lock; this supersedes any pip reference.
- Library grounding (mandatory): before writing or changing any code that calls a library
  in docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries (or any indexed library), query the framework graph for the exact symbol
  and signature. Do not call a method you have not confirmed exists in the index. If you
  cannot confirm it, search again or ask. Do not guess an API. Two tools, two jobs: the code
  graph (Graphify) is the grounding authority for what exists and its signature; the
  LangChain docs MCP server (hosted, current) is for understanding concepts and how a feature
  is meant to be wired. Order: docs first to understand, code to confirm, then write. A
  method name seen in docs or an example is not confirmation; confirm it against the code graph.
- Per task, in order: define the contract, write the failing test against it, implement to
  green, refactor, run the task's verify step, then record an ADR if an architectural
  decision was made and update docs.
- Keep ADRs short: context, decision, consequences, a few lines each.
- Commit after every approved task: code, tasks.md status, any ADR, and docs in one commit,
  with a message naming the task and its requirement and acceptance criterion. Start each
  task from a clean working tree. Never commit secrets or generated data. Committed files
  are the source of truth on resume.
- Structured output on open models: many open models (the Qwen and DeepSeek ladder) reject a
  forced tool or schema choice in thinking or reasoning mode. Configure structured output
  through the model-profile seam keyed by model id (the method, default function_calling, and
  a structured-only extra body that disables thinking on the forced call only), never by
  assuming the model supports it. Provider flags are empirical, live in the profile config and
  a dated ADR, and are never hardcoded in node code. If reasoning then emitting a contract is
  two separable checkable steps, split into a reasoning node and a structured-emit node and
  prefer that at high control; otherwise use the profile.
```

(The fuller CLAUDE.md, with the per-task human-in-the-loop gate, the cross-session memory, the environment rule, and the boundaries, is maintained as its own file. This block is the architecture-and-discipline summary that the playbook depends on.)

---

## 4. The build workflow

Each phase has a human review gate. Do not advance until the current phase is approved.

**Phase 1, Plan.** Invoke planning-and-task-breakdown against ``RAG_Capability_Spec.md`` in read-only Plan Mode. Output a `plan.md`: the major components, the build order, what is sequential versus parallelizable, risks, and verification checkpoints. Human reviews.

**Phase 2, Tasks.** Break the plan into tasks, each touching no more than about five files, each with explicit acceptance criteria and a verify step. Output `tasks.md` as a checklist, with each task mapped to the requirement and acceptance criterion it implements (using the scheme in `project structure in section 10; requirements FR-S, FR-C, FR-I, FR-Q`). Human reviews.

**Phase 3, Contracts.** Before implementation, define the input and output contracts for the components (Pydantic models on the default stack) in `src/contracts/`. These are frozen first because the tests in Phase 4 assert against them, and because they may also serve as `the registered capability contracts each capability is registered under`. Human reviews the contracts.

**Phase 4, Contract-first TDD build, per task in dependency order.** For each task:
1. Ground the library calls: query the `framework` graph for the exact symbols and signatures the task needs (library-grounding rule).
2. Write the failing test against the task's contract (TDD red).
3. Implement the minimum to pass, one task at a time (incremental-implementation), to green.
4. Refactor.
5. Run the task's verify step.
6. If an architectural decision was made, record a short ADR. Update docs.
7. After approval, commit the task atomically (code, `tasks.md` status, any ADR, docs) with a message naming the task and its requirement and acceptance criterion. Each task starts from a clean, committed working tree, so a failed attempt can be discarded with git.

context-engineering keeps each task's window to the relevant spec section plus the grounded API, not the whole spec.

**Phase 5, Integrate and end to end.** Wire the components together as the spec defines, run the end-to-end scenario or scenarios and any dry run, and confirm the acceptance criteria. The specific integration wiring is project-defined; follow the spec.

debugging-and-error-recovery is invoked only when a task gets stuck.

---

## 5. Indexing and the grounding discipline

The index is built once (Phase 0) and updated incrementally. The discipline is what makes it work, since an index the agent does not consult changes nothing.

- Two separate graphs: `framework` (the libraries in `docling, the ArcadeDB client, the embedding and reranker clients, the OpenRouter client, the MCP server framework, and the extraction libraries`, source plus docs) and `project` (the repo). Separate graphs keep library lookups from being diluted by project code and vice versa, while keeping the framework stack together so its cross-package relationships stay traversable.
- The consultation rule lives in CLAUDE.md (section 3) and is repeated as step 1 of every Phase 4 task, so it fires on every build task rather than being a phase the agent might skip.
- The SessionStart hook surfaces graph status at the top of each session so the agent knows the index is available.
- Update the graphs after human edits and after the agent's own commits, so the map stays current.
- Onboarding a new library into the framework index (the recipe, earned across vLLM, ArcadeDB, and FastMCP): `uv add` the library, then get its API into the index by one of three routes, then rebuild and query before writing the call. Route by the library's shape: (1) pip-installed and stable, add it to the site-packages package list the refresh script indexes; (2) not pip-installable on the host, or fast-moving, or a monorepo where the layout matters, clone-stage it (clone the repo, add a staging block that rsyncs the core package, `git pull` to track releases, hold it at the tag matching the installed version); (3) no source tree and no docs MCP, LLM-extract the vendor docs into the same index (the ADR-0008 pattern). Two rules across all routes: ground against both the code graph and the installed `inspect.signature` (a clone at latest can drift from the pinned install), and watch transitive dependencies (a new library can pull in a package that flips the behavior of code that gates on whether something is importable, so gate such activation on real configuration, not mere importability).

If, in practice, the graph is strong at structure but weak at returning an exact signature, add an embedding-based semantic search alongside it (for example code-review-graph), or fall back to having the agent open the specific symbol definition the graph points to.

Two retrieval surfaces, matched to two data shapes. Graphify's structural extraction is the code index and the grounding authority: it is exact for code because code has real structure, and grounding resolves against it. The LangChain docs MCP server is the documentation surface: hosted and current, it explains concepts and how a feature is meant to be wired, which is often what unblocks understanding a middleware or a runtime feature. Keep them in their lanes. Do not switch on LLM-based doc extraction to build a unified code-and-docs index: it is expensive in LLM calls, it goes stale the moment the library version moves, and it degrades the part of the index that most needs to be exact, since extracting prose into a graph is lossy and nondeterministic. The hosted docs MCP is current and free, so documentation stays the vendor's job and the code graph stays ours. The ordering on any unfamiliar surface is docs first to understand, code to confirm the exact symbol and signature in the installed version, then write the call. Never let a method name seen in prose or an example substitute for that confirmation.

---

## 6. Example prompts to Claude Code

```
Phase 0:
  "Read `RAG_Capability_Spec.md`. Scaffold the folder hierarchy exactly as in the structure section. Do
   not write any implementation yet."

Phase 1:
  "In Plan Mode, read `RAG_Capability_Spec.md` and produce plan.md per the planning-and-task-breakdown
   skill. Do not write code."

Phase 2:
  "Break plan.md into tasks.md per the planning skill. Each task lists the requirement and
   acceptance criterion it implements, its acceptance criteria, files touched, and a verify
   step."

Phase 3:
  "Define the input and output contracts for all components in src/contracts/, matching the
   component descriptions in `RAG_Capability_Spec.md`. These Pydantic contracts are what each capability is registered under.. Do not implement yet."

Phase 4 (per task):
  "Take the next task in tasks.md. First query the framework graph for the exact API you
   will use and show me what you found. Then write the failing test against the contract,
   then implement to green, then run the verify step. Record an ADR if you made an
   architectural decision."
```

---

## 7. What not to do

- Do not install every skill and let process overhead slow the build. The active loop is plan, tasks, contracts, contract-first TDD, with documentation and ADRs kept terse and debugging on demand. This matters most at `MVP` of demo or MVP; a production build can afford more.
- Do not let the agent write library code without grounding it first. That is the one rule worth being strict about.
- Do not skip the contracts phase. Frozen contracts are what let the tests come before the code.
- Do not assume an open model supports forced structured output. Models in thinking or reasoning mode (the Qwen and DeepSeek ladder) reject a forced tool or schema choice and will return a 400. Configure structured output through the model-profile seam (the method, default function_calling, and a structured-only extra body that disables thinking on the forced call only), record the provider flags in a profiles config and a dated ADR, and never hardcode a flag in agent code. Determine the flags empirically against the live API, since they drift between providers and over time. If the reasoning and the structured emission are two separable, checkable steps, split the node into a reasoning node and a structured-emit node rather than reaching for the profile, and prefer that split at high control for auditability.
- On the default Python stack, never let the agent use pip or a bare `python` command. Every run, test, and debug command is `uv run`, and every install is `uv add`. When a command fails with a missing-module or missing-environment error, the cause is a forgotten `uv run` prefix or an undeclared dependency, not a broken environment. Re-run with `uv run` or `uv add` the dependency. Do not let it spiral into reinstalling libraries or recreating the environment.

---

## How to reuse

1. Copy this file, fill in the Project Parameters table for the new engagement, and delete the worked example if you like.
2. This playbook is already filled for RAG_Wright; no placeholders remain.
3. Adjust the stack slots only if the project is not the default Python with uv. Everything else, the skills, the phases, the per-task loop, the grounding discipline, the memory and commit rules, stays as written.
