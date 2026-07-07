"""Merge the committed ArcadeDB docs extraction into the framework graph (additive, no LLM).

Called by scripts/refresh_framework_graph.sh after the Python-AST build. `build_merge` is
deterministic and free; the LLM cost was paid once by scripts/extract_arcadedb_docs.py producing the
committed artifact. `dedup=False` keeps the merge purely additive: the doc nodes are added and the
Python-AST grounding is left untouched (fuzzy dedup could wrongly merge distinct code symbols).

Run with graphify's interpreter:
    $(cat graphify-out/.graphify_python) scripts/merge_docs_into_framework.py graphify-out/framework/graph.json
"""

import json
import sys
from pathlib import Path

from networkx.readwrite import json_graph

import graphify.build as gb

ARTIFACT = Path("docs/vendor/arcadedb/arcadedb-docs-extraction.json")


def main() -> None:
    graph_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("graphify-out/framework/graph.json")
    if not ARTIFACT.is_file():
        print(f"[merge] no docs artifact at {ARTIFACT}; skipping (framework graph is Python-only)")
        return
    if not graph_path.is_file():
        raise SystemExit(f"[merge] framework graph not found at {graph_path}")

    before = len(json.loads(graph_path.read_text(encoding="utf-8")).get("nodes", []))
    extraction = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    # dedup=False -> additive: add the doc nodes, leave the Python-AST nodes intact.
    graph = gb.build_merge([extraction], graph_path=graph_path, dedup=False)
    data = json_graph.node_link_data(graph, edges="links")
    graph_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    print(f"[merge] ArcadeDB docs merged: {before} -> {graph.number_of_nodes()} nodes ({graph_path})")


if __name__ == "__main__":
    main()
