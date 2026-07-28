"""KG-4: build the CUAD clause-extraction cache from the already-segmented CUAD spans (`ragwright_cuad`).

Leg A needs multi-clause CONTRACTS; CUAD is already segmented (102 contracts / 27,074 function-tagged spans,
with contract_id + doc offsets). This reads those spans, keeps the SUBSTANTIVE clause functions (drops NONE
and the pure-metadata tags -- Parties/Document Name/Agreement Date/Effective Date, which have no clause
properties), and writes a cache `populate_clause_kg` consumes:

  {clause_id, function, text, span_id}

`clause_id` = ChunkId.of(contract_id, span_index, text) -- a valid, deterministic, CONTRACT-SCOPED id, so
Leg A can scope clauses to one contract via the `<contract_id>:...` prefix and the store write stays keyed to
the same contract the CUAD spans belong to. `span_id` is carried for the citation (doc offsets live on the
span). No LLM, no network beyond the store.

  uv run python -m scripts.build_cuad_clause_cache        # -> data/models/cuad_clause_cache.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from rag_wright.contracts.function import FUNCTION_LABEL_SET
from rag_wright.contracts.identifiers import ChunkId

OUT = Path("data/models/cuad_clause_cache.jsonl")
DB = "ragwright_cuad"
_PAGE = 20000  # the client caps a single read; page through with SKIP/LIMIT

# Pure-metadata tags with no clause properties -> excluded from property extraction (KG-4 flag). Expiration
# Date is KEPT (it carries a temporal bound).
_METADATA_TAGS = {"Parties", "Document Name", "Agreement Date", "Effective Date"}


def _iter_spans(store):
    from rag_wright.store.arcadedb import SPAN_TYPE

    offset = 0
    while True:
        rows = store._query(
            f"SELECT contract_id, span_index, function, text, span_id FROM {SPAN_TYPE} "
            f"SKIP {offset} LIMIT {_PAGE}"
        )
        if not rows:
            return
        yield from rows
        if len(rows) < _PAGE:
            return
        offset += _PAGE


def main() -> None:
    load_dotenv()
    from rag_wright.store.arcadedb import ArcadeDBStore

    store = ArcadeDBStore.from_env(database=DB, reset=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    seen, kept, skipped_meta, skipped_none, skipped_empty = 0, 0, 0, 0, 0
    with OUT.open("w", encoding="utf-8") as f:
        for r in _iter_spans(store):
            seen += 1
            fn = (r.get("function") or "").strip()
            text = (r.get("text") or "").strip()
            if not text:
                skipped_empty += 1
                continue
            if fn == "NONE" or fn not in FUNCTION_LABEL_SET:
                skipped_none += 1
                continue
            if fn in _METADATA_TAGS:
                skipped_meta += 1
                continue
            clause_id = str(ChunkId.of(r["contract_id"], int(r["span_index"]), text))
            f.write(json.dumps({
                "clause_id": clause_id, "function": fn, "text": text, "span_id": r["span_id"],
            }) + "\n")
            kept += 1

    store.close()
    print(f"spans read: {seen}")
    print(f"  kept (substantive clauses): {kept}")
    print(f"  skipped NONE/out-of-taxonomy: {skipped_none}")
    print(f"  skipped metadata tags: {skipped_meta}")
    print(f"  skipped empty text: {skipped_empty}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
