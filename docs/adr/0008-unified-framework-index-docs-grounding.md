# ADR-0008: Unified framework index — LLM-extracted docs alongside Python AST

Date: 2026-07-07. Status: Accepted. Establishes how a framework surface that the Python source tree
does not cover — and that has no MCP server — is grounded: by LLM-extracting its vendor docs into the
same single Graphify framework index. First applied to the ArcadeDB SQL vector functions.

## Context

The framework graph grounds every library call the capabilities make (CLAUDE.md library-grounding
rule). It is built by `scripts/refresh_framework_graph.sh` as pure Python **AST** extraction of the
installed packages (`docling docling_core FlagEmbedding langchain_openai mcp spacy arcadedb_python`).
That was the right default because the sibling projects' fast-moving libraries (LangChain/LangGraph/
Deep Agents) ship their own MCP servers, so their docs come from MCP and only the Python source
needs indexing.

The ArcadeDB store exposed the gap. Its **client** is `arcadedb-python` (Python, AST-covered). But its
**SQL vector functions** — `vector.fuse`, `vector.sparseNeighbors`, `vector.neighbors`, and the
`LSM_VECTOR` / `LSM_SPARSE_VECTOR` DDL — live only in the server and its docs. There is no Python
source for them and no ArcadeDB MCP. Grounding them by reverse-engineering the server jar or trial SQL
is exactly what the grounding discipline forbids.

## Decision

When a framework surface has no source-tree coverage and no MCP, grounded it by **LLM-extracting its
relevant vendor docs into the same single framework index** (docs + Python, one graph), using the
project's model path. This keeps one unified framework index and one grounding authority, and keeps
cost bounded by extracting a tight, relevant doc scope once.

Applied to ArcadeDB (vector surface only, for now):

1. **Corpus** — the vector-surface doc pages, fetched to committed markdown under
   `docs/vendor/arcadedb/` (via pandoc from the rendered pages, since Graphify's html2text dropped the
   JS-tabbed hybrid code blocks). Scope: the `vector-embeddings` how-to and `vector-search` tutorial
   (they carry `vector.fuse` / `vector.sparseNeighbors` / `vector.neighbors` / the index DDL). The
   general `sql-indexes` reference is out of the vector scope and was dropped; broader SQL grounding
   (for T25/T26) is deferred.
2. **Extraction (cost-once)** — `scripts/extract_arcadedb_docs.py` sends the corpus to **OpenRouter /
   DeepSeek V4 Pro** (the same model the pipeline uses; ADR-0006) via Graphify's `openrouter` backend
   and writes a committed extraction artifact `docs/vendor/arcadedb/arcadedb-docs-extraction.json`.
   Reasoning stays ON — T12/ADR-0006 established DeepSeek V4 Pro does reasoning and structured output
   together — so the earlier truncation was fixed by small chunks (JSON fits the default output
   budget), not by disabling thinking.
3. **Merge (free, every rebuild)** — `refresh_framework_graph.sh` calls
   `scripts/merge_docs_into_framework.py`, which `build_merge`s the committed artifact into the
   Python-AST graph with `dedup=False` (purely **additive**: the doc nodes are added, the AST nodes
   are untouched; fuzzy dedup could wrongly merge distinct code symbols).

Result: the single `graphify-out/framework/graph.json` holds both lanes — the `arcadedb-python` client
(AST) and the ArcadeDB SQL vector functions (docs). `graphify query "vector.fuse ..."` resolves.

### Graphify OpenRouter backend

Graphify uses a **custom provider** registered in `~/.graphify/providers.json` (user-global, trusted):

```json
{ "openrouter": { "base_url": "https://openrouter.ai/api/v1", "env_key": "OPENROUTER_API_KEY",
                  "default_model": "deepseek/deepseek-v4-pro", "temperature": 0, "max_tokens": 16384,
                  "pricing": {"input": 0.0, "output": 0.0} } }
```

plus Graphify's OpenAI extra (`uv tool install "graphifyy[openai]"`). The key is read from the project
`.env`. This is Graphify's own tool config, separate from the project's model-profile seam.

## Consequences

- **Cost is paid once.** The LLM extraction runs only via `extract_arcadedb_docs.py`, re-run only when
  `docs/vendor/arcadedb/` changes. Every ordinary `refresh_framework_graph.sh` rebuild is
  AST (free) + `build_merge` (free). `graphify-out/` stays gitignored/regenerable; the corpus and the
  extraction artifact are committed so a fresh checkout rebuilds the unified index with no LLM cost.
- **One index, one authority.** No second tool, no separate docs graph to keep in sync. The
  session-start status line names both lanes.
- **The policy generalizes.** Any future framework surface with no source and no MCP is grounded the
  same way: tight committed corpus → cost-once OpenRouter/DeepSeek extraction → additive merge.
- The ArcadeDB SQL grounding replaces the interim doc-fetch-and-ADR record in ADR-0007; ADR-0007's SQL
  facts remain correct and are now also queryable in the framework graph.

## Addendum (2026-08-07): the full new-library onboarding decision tree

This ADR started as the "no source, no MCP → extract the vendor docs" case. Two more experiences (vLLM at
ADR-0039, FastMCP at MCP-PROTO) rounded it into a complete recipe for onboarding **any** new library into the
one framework grounding index. The decision tree, keyed by the library's shape:

1. **Pip-installed and stable** — add the package to the `PKGS` list in `refresh_framework_graph.sh`. It is
   staged from site-packages and version-matched to the installed dependency by construction. This is the
   default (docling, langchain_openai, mcp, langgraph, deepagents, langchain_mcp_adapters, ...).
2. **Not pip-installable on this host, OR fast-moving, OR a monorepo where the layout matters** — clone-stage
   it. `graphify clone <github-url>`, then a staging block in `refresh_framework_graph.sh` that rsyncs the core
   package (excluding tests/bytecode) into the extraction stage. Refresh with `git -C <clone> pull`; hold the
   clone at the tag matching the installed version so the graph matches what the code calls. Applied to:
   - **vllm** — not pip-installable on the Mac (CUDA/Linux); staged from `~/.graphify-src/vllm`, scoped to the
     serving/sampling/structured-output surfaces our code drives, not the kernel internals.
   - **fastmcp** — releases fast (the reason repo-level grounding matters) and ships a 3.x monorepo whose core
     package is under `fastmcp_slim/fastmcp`; staged from `~/.graphify/repos/jlowin/fastmcp`.
3. **No source tree AND no docs MCP** — LLM-extract the vendor docs into the same index (the original body of
   this ADR: tight committed corpus under `docs/vendor/` → cost-once OpenRouter/DeepSeek extraction → free
   additive merge on every rebuild). Applied to the ArcadeDB SQL vector functions.

Two rules hold across all three routes:

- **Ground against BOTH the code graph AND the installed `inspect.signature`.** On a fast-moving library a
  clone at latest can drift from the pinned install; confirming the signature in the running interpreter closes
  the gap. (FastMCP was confirmed against both the cloned-repo AST graph and the installed 3.4.6 signatures
  before a line was written.)
- **Watch transitive dependencies.** A new library can pull in a package that flips the behavior of code that
  gates on whether something is merely importable. Adding `fastmcp` pulled in `opentelemetry-api` transitively,
  which activated the observability seam (it had gated on `import opentelemetry` succeeding). The fix, and the
  general rule: gate such activation on **real configuration** (here, a real tracer provider being installed),
  not on mere importability, so a transitive dependency never changes runtime behavior.

The invariant is unchanged from the original decision: **one index, one authority.** The three routes are just
how a surface gets *into* that one index; grounding still resolves against `graphify-out/framework/graph.json`.
