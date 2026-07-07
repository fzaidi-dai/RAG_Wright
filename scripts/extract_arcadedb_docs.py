"""Re-extract the ArcadeDB vector-surface docs into the committed extraction artifact.

Run this ONLY when the ArcadeDB doc corpus under docs/vendor/arcadedb/ changes. It is the one
LLM-costing step of the framework grounding: it sends the docs to OpenRouter (DeepSeek V4 Pro, the
same model the pipeline uses) and writes the extraction to a committed JSON artifact. The framework
build (scripts/refresh_framework_graph.sh) then merges that artifact for free on every rebuild, so
this cost is paid once, not per rebuild.

Grounding rationale: the ArcadeDB *client* (arcadedb-python) is Python and is covered by the
framework graph's AST extraction. The ArcadeDB *SQL vector functions* (vector.fuse,
vector.sparseNeighbors, vector.neighbors, the LSM_VECTOR/LSM_SPARSE_VECTOR DDL) live only in the
server and its docs, with no Python source and no MCP — so they are grounded here by LLM-extracting
the vendor docs into the same single framework index (docs + Python), per the playbook.

Requires the `openrouter` provider in ~/.graphify/providers.json and graphify's openai extra
(`uv tool install "graphifyy[openai]"`). Reads OPENROUTER_API_KEY from the project .env.

Run with graphify's interpreter:
    $(cat graphify-out/.graphify_python) scripts/extract_arcadedb_docs.py
"""

import json
import os
import pathlib

# Load OPENROUTER_API_KEY (and any other keys) from the project .env for graphify's tool process.
for _line in pathlib.Path(".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

import graphify.llm as gllm  # noqa: E402  (after .env load)

CORPUS = pathlib.Path("docs/vendor/arcadedb")
ARTIFACT = CORPUS / "arcadedb-docs-extraction.json"


def main() -> None:
    docs = sorted(CORPUS.glob("arcadedb-*.md"))
    if not docs:
        raise SystemExit(f"no docs found under {CORPUS}")
    print("extracting:", [d.name for d in docs], flush=True)

    # DeepSeek V4 Pro reasons AND emits JSON in one response (ADR-0006 / T12: both together, no
    # thinking disable). Keep chunks small so each JSON stays under the default 16384 output budget
    # (no truncation) and the per-chunk reasoning stays bounded (a large output cap makes it reason
    # without end and effectively stalls).
    result = gllm.extract_corpus_parallel(
        docs, backend="openrouter", chunk_size=1, max_concurrency=2, token_budget=6000
    )
    nodes, edges = result.get("nodes", []), result.get("edges", [])
    failed = result.get("failed_chunks")
    print(
        f"nodes={len(nodes)} edges={len(edges)} failed={failed} "
        f"in_tok={result.get('input_tokens')} out_tok={result.get('output_tokens')}",
        flush=True,
    )
    if failed:
        raise SystemExit(f"extraction had {failed} failed chunk(s); not overwriting the artifact")

    ARTIFACT.write_text(
        json.dumps(
            {"nodes": nodes, "edges": edges, "hyperedges": result.get("hyperedges", [])},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("wrote", ARTIFACT)


if __name__ == "__main__":
    main()
