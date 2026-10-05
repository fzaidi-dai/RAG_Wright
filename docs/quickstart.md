# Quickstart

Open a workspace, ingest a document, and ask a cited question — entirely through `rag_wright.api`. The runnable
source is [`examples/quickstart.py`](../examples/quickstart.py); this page walks through it.

## Prerequisites

- **ArcadeDB running** with `ARCADEDB_*` in a `.env` at the repo root — see [`installation.md`](installation.md).
- **A model provider** — `OPENROUTER_API_KEY` in `.env` (default), or `RAG_SERVING=vllm` + `VLLM_BASE_URL`/`VLLM_API_KEY`.

Run it:

```sh
uv run python examples/quickstart.py
```

## The five moves

### 1. Opt into a capability catalog

The engine ships an **empty** ARD catalog. The quickstart opts into the bundled contract/compliance reference pack
so its subgraphs are registered; a real product registers its own domain capabilities instead.

```python
from rag_wright.api import load_reference_pack
load_reference_pack()
```

### 2. Configure and open a workspace

Everything is imported from `rag_wright.api`. `corpus` is the backend database name; `reset=True` gives a fresh DB.

```python
from rag_wright.api import EngineConfig, StoreConfig, open_workspace

config = EngineConfig(store=StoreConfig(
    host="localhost", port="2480", user="root", password="<DEV_PASSWORD>"))
ws = open_workspace(config, corpus="quickstart_demo", reset=True)
```

### 3. Ingest a document

`source_document` makes a text-only document (use `parse_document` for a PDF). The ingestion subgraph runs the
whole front-end — chunk → segment → classify → extract → embed → write the typed clause KG.

```python
from rag_wright.api import source_document, ainvoke_subgraph
import tempfile

doc = source_document("ACME_MSA", text=CONTRACT_TEXT)
with tempfile.TemporaryDirectory() as cache:
    await ainvoke_subgraph(
        "contract_ingestion_pipeline", {"document": doc, "cache_dir": cache}, resources=ws)
```

### 4. Inspect the knowledge graph

The ingest split the document into provisions and wrote one typed clause per provision, each carrying its
operative-span anchor:

```python
from rag_wright.api import kg_read
clauses = kg_read(ws, "Clause", fields=["clause_id", "function"])
# -> e.g. functions ['Payment Terms', 'Confidentiality', 'Cap On Liability', 'Expiration Date', 'Governing Law']
```

### 5. Ask a question — get a cited answer

Scoped to the ingested document; the answer is grounded in the retrieved clauses and **cited by `span_id`** (or the
capability abstains rather than guess):

```python
out = await ainvoke_subgraph(
    "intra_document_qa",
    {"contract_id": "ACME_MSA", "question": "What is the liability cap?"},
    resources=ws)
print(out["answer"].answer, out["answer"].citations)
```

Expected (grounded + cited):

```
The aggregate liability for either party is capped at the total fees paid by the Customer to the Provider in the
twelve (12) months preceding the claim, with the exception of breaches of confidentiality.
cited spans: ['ACME_MSA:0:…#4']
```

For corpus-wide retrieval (not scoped to one document), use `typed_property_retrieval` with `{"query": "…"}`, which
returns ranked spans cited by `span_id` and labelled with each clause's function.

## Measuring usage

Wrap any call path in `measure_usage()` to get model calls and cost in-band (no Langfuse round-trip):

```python
from rag_wright.api import measure_usage
with measure_usage() as usage:
    ...  # ingest + query
print(usage.calls, usage.cost_usd)
```

## Next

- [Concepts](concepts.md) and [Architecture](architecture.md) — the model and the structure.
- [Configuration](configuration.md) — every `EngineConfig` field and the model-profile seam.
- [Building a new domain](domain-adaptation/) — author your own `.ttl` pack and capabilities instead of the reference pack.
