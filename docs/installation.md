# Installation

RAG_Wright is a Python package with two runtime prerequisites you provide: a **store** (ArcadeDB) and a **model
provider** (OpenRouter or a self-hosted open-model endpoint). Configuration is covered in
[`configuration.md`](configuration.md); this page gets you to a working install.

## Prerequisites

- **Python 3.12.x** (`requires-python = ">=3.12,<3.13"`).
- **[uv](https://docs.astral.sh/uv/)** — the engine is uv-native; use `uv add`/`uv run`, never bare `pip`/`python`.
- **Docker** — to run ArcadeDB locally.
- **A model provider** — an OpenRouter API key (default), or a self-hosted vLLM endpoint.

## Install the engine

```sh
uv add rag-wright
```

That installs the latest PyPI release (0.1.0). Work merged after it (for example the generic `build_ingestion`
builder, the neutral default schema, and the reference pack's move to `rag_wright.packs`) is on `main` until the next
batched release ([`releasing.md`](releasing.md)); to use it now, or to develop the engine and a product side by side, depend on it by path or git instead:

```toml
# pyproject.toml of the product
[tool.uv.sources]
rag-wright = { path = "../RAG_Wright", editable = true }   # or: { git = "https://github.com/fzaidi-dai/RAG_Wright" }
```

Working on the engine itself:

```sh
uv sync                 # create the environment from pyproject.toml + uv.lock
uv run pytest           # hermetic suite (store/model/parse tests are opt-in markers)
uv run ruff check .     # lint
```

The default suite is hermetic and enforces it: `tests/conftest.py` fails any test that is not marked live and tries
to reach the network (mock the call, or mark the test). The live markers, declared in `pyproject.toml`, are `model`,
`store`, `parse`, `embed`, `rerank`, `ner` and `fleet`; run one with `uv run pytest -m <marker>`.

### Optional extras

- **`rag-wright[ner]`** — spaCy NER (named entity recognition). The library is the extra; the model is a runtime
  download (it is not on PyPI):
  ```sh
  uv add 'rag-wright[ner]'                         # in a product; working on the engine itself: uv sync --extra ner
  uv run python -m spacy download en_core_web_sm   # configurable via RAG_SPACY_MODEL
  ```
- **`rag-wright[ocr-bench]`** — the OCR-benchmark extras (`mlx-vlm`, `ocrmac`, `onnxruntime`, `scikit-image`).

### Skills for coding agents

The engine ships its Claude Code skills (`using-the-rag-wright-engine`, `building-an-ingestion-capability`,
`authoring-a-capability`, `creating-evals`, `classifier-opportunity-analysis`, `setfit`, `laya`, `qwen-vllm-modal`)
inside the package, at `rag_wright/.agents/skills/`, so they match the installed version. Link them into a product
repo's `.claude/skills/`, and re-run this after every engine upgrade:

```sh
SKILLS=$(uv run python -c "import pathlib, rag_wright; print(pathlib.Path(rag_wright.__file__).parent / '.agents' / 'skills')")
mkdir -p .claude/skills && for d in "$SKILLS"/*/; do ln -sfn "${d%/}" ".claude/skills/$(basename "$d")"; done
```

## Run ArcadeDB (the single store)

One ArcadeDB database holds both the hybrid retrieval index and the knowledge graph. Pinned to **26.7.1** (do not
use `:latest` — it is a moving snapshot). Full detail in [`ArcadeDB_Local.md`](ArcadeDB_Local.md).

```bash
docker run -d --name arcadedb-ragwright \
  -p 2480:2480 -p 2424:2424 \
  -v "$PWD/data/arcadedb:/home/arcadedb/databases" \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<DEV_PASSWORD> -Darcadedb.server.mode=development" \
  -e ARCADEDB_OPTS_MEMORY="-Xms2G -Xmx6G" \
  arcadedata/arcadedb:26.7.1
```

- Port **2480** is the HTTP API the client uses (2424 is the binary protocol). Data persists under the gitignored
  `data/arcadedb/`.
- **JVM heap is the capacity cap.** It is set by the container's `ARCADEDB_OPTS_MEMORY` (read at container start,
  so changing it means recreating the container). The image's default 2 GB ran out of memory part-way through the
  510-contract CUAD knowledge graph; `-Xmx6G` above holds it. Raise it for a larger corpus. An under-sized heap shows up as a read-lock error mid-write (an out-of-memory error, OOM, in
  `docker logs`) on a full ingest.
- Verify it is up:
  ```bash
  curl -s -u root:<DEV_PASSWORD> http://localhost:2480/api/v1/databases
  ```

## Configure access (`.env`)

Secrets and connection details live in a gitignored `.env`. The minimum:

```sh
# store
ARCADEDB_HOST=localhost
ARCADEDB_PORT=2480
ARCADEDB_USER=root
ARCADEDB_PASSWORD=<DEV_PASSWORD>
ARCADEDB_DATABASE=rag_wright

# model provider (default: OpenRouter)
OPENROUTER_API_KEY=sk-or-...
```

To serve open models yourself instead, set `RAG_SERVING=vllm` with `VLLM_BASE_URL` + `VLLM_API_KEY` (see
[`configuration.md`](configuration.md)).

## Upgrading a database from an earlier engine version

`open_workspace` (through `ensure_schema`) refuses a database whose `Span` type still has the old field names
(`contract_id`, `function`, `functions`; renamed to `document_id`, `primary_tag`, `tags`) and raises a `RuntimeError`
naming the fix. Migrate each such database once, in place (it connects with the `ARCADEDB_*` settings in `.env`; it
is idempotent and streams `X/N` progress):

```sh
uv run python -u scripts/migrate_span_fields.py <database>
```

Every other breaking change since 0.1.0, with the exact change a consumer makes, is in
[`specs/ingestion-hooks/ing8-breaking-changes.md`](specs/ingestion-hooks/ing8-breaking-changes.md).

## Verify

With ArcadeDB up and a provider key set, follow [`quickstart.md`](quickstart.md) to open a workspace, ingest a
document, and get a cited answer. Store-backed tests are opt-in:

```sh
uv run pytest -m store        # runs the live ArcadeDB tests
```

A non-live test that reaches the network fails by design (see "Working on the engine itself" above).
