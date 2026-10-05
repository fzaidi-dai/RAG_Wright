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

For a product repo that pins the engine before it is published to an index, depend on it by path or git instead:

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

### Optional extras

- **`rag-wright[ner]`** — spaCy NER. The library is the extra; the model is a runtime download (it is not on PyPI):
  ```sh
  uv pip install 'rag-wright[ner]'
  uv run python -m spacy download en_core_web_sm   # configurable via RAG_SPACY_MODEL
  ```
- **`rag-wright[ocr-bench]`** — the OCR-benchmark extras (`mlx-vlm`, `ocrmac`, `onnxruntime`, `scikit-image`).

## Run ArcadeDB (the single store)

One ArcadeDB database holds both the hybrid retrieval index and the knowledge graph. Pinned to **26.7.1** (do not
use `:latest` — it is a moving snapshot). Full detail in [`ArcadeDB_Local.md`](ArcadeDB_Local.md).

```bash
docker run -d --name arcadedb-ragwright \
  -p 2480:2480 -p 2424:2424 \
  -v "$PWD/data/arcadedb:/home/arcadedb/databases" \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<DEV_PASSWORD> -Darcadedb.server.mode=development" \
  arcadedata/arcadedb:26.7.1
```

- Port **2480** is the HTTP API the client uses (2424 is the binary protocol). Data persists under the gitignored
  `data/arcadedb/`.
- **JVM heap is the capacity cap.** For a large corpus raise it via `JAVA_OPTS` (e.g. `-Xmx6g`); an under-sized
  heap triggers a read-lock crash (OOM) on a full ingest.
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

## Verify

With ArcadeDB up and a provider key set, follow [`quickstart.md`](quickstart.md) to open a workspace, ingest a
document, and get a cited answer. Store-backed tests are opt-in:

```sh
uv run pytest -m store        # runs the live ArcadeDB tests
```
