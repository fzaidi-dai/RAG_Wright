"""LIVE smoke for the table-content retrieval fix (engine issue 0014 / ADR-0069).

Reproduces the issue's EXACT case against the real ingest + a real ArcadeDB KG + the real BGE embedder + the real
OpenRouter answer model: a born-digital PDF whose fee figure (48,000) lives ONLY in a table cell. Before the fix
the table was parsed into `.text` but NEVER chunked/indexed (docling puts it in `document.tables`, not
`document.texts`, and the chunker read only `.texts`), so it ingested with a clean report and no table question
could be answered -- a SILENT retrieval loss. After the fix the chunker walks the reading-order body (text +
tables + figures), so the fee table folds into its section's chunk, is segmented as one ATOMIC span (header + all
rows), embedded, indexed, and retrievable with a citation.

  BEFORE ADR-0069: "What is the fee for the Enterprise tier?" -> abstain / not_found (table never reached a span).
  AFTER:           the fee table is a retrievable span -> the real answer model returns 48,000 with a citation,
                   and the Limitation-of-Liability prose (the control) still answers (no regression).

Exits non-zero if the table figure is not retrievable or the liability control stops answering (a live guard).

  uv run --no-sync python -m scripts.table_retrieval_smoke
  COMPLIANCE_DB=ragwright_issue0014 uv run --no-sync python -m scripts.table_retrieval_smoke
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "table-bearing-contract.pdf"


def log(m: str) -> None:
    print(m, flush=True)


async def main() -> int:
    load_dotenv()
    os.environ.setdefault("RAG_SERVING", "openrouter")

    from rag_wright.capabilities.contract_kg_serve import contract_clause_index
    from rag_wright.capabilities.dg_extraction import build_verified_registry
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.contract_ingestion_pipeline import aparsed_source_document, aproduction_document_ingest
    from rag_wright.subgraphs.intra_document_qa import production_intra_document_qa, rehydrate_clause_texts

    db = os.environ.get("ISSUE0014_DB", "ragwright_issue0014")
    doc_id = "table-bearing-contract"
    cache = Path(tempfile.mkdtemp(prefix="issue0014_"))  # fresh cache -> a real parse+chunk+embed every run
    log(f"[smoke] 1/4 fresh ArcadeDB {db!r} + fresh cache {cache}")
    store = ArcadeDBStore.from_env(database=db, reset=True)
    store.ensure_schema()

    ingest = aproduction_document_ingest(
        store, cache_dir=cache, registry=build_verified_registry({"entities": []}), party_seed_path=None)

    log("[smoke] 2/4 parse the born-digital PDF (the fee figure lives ONLY in a table cell)")
    document = await aparsed_source_document(doc_id, _FIXTURE.name, _FIXTURE.read_bytes(), cache_dir=cache)
    assert "48,000" in document.text and "|" in document.text, "fixture: table not in parsed .text"
    log(f"[smoke]   parsed: .text has the table figure (48,000): {'48,000' in document.text}")

    log("[smoke] 3/4 ingest (real chunk -> segment -> classify -> index -> clause KG); report claims no loss")
    out = await ingest.ainvoke({"document": document})
    if out.get("dead_letter"):
        log(f"[smoke]   DEAD-LETTER: {out['dead_letter']}")
        store.close()
        return 1
    written = out.get("written", {})
    log(f"[smoke]   written={written}  span_failures={out.get('span_failures') or []}  "
        f"clause_failures={out.get('clause_failures') or []}")

    # 4a. RETRIEVAL-LAYER proof (deterministic, answer-model-independent -- THE 0014 fix): the fee table reaches
    # the served, rehydrated evidence pool. Before the fix it was never chunked/indexed, so no served clause
    # carried it; after, its atomic table span is a served clause whose rehydrated text holds the figure.
    log("[smoke] 4/4 (a) retrieval-layer proof: the table span reaches the served evidence pool")
    served = contract_clause_index(store, doc_id, include_untyped=True)
    texts = rehydrate_clause_texts(store, doc_id, served)
    table_clauses = [c for c in served if "48,000" in (texts.get(c.clause_id) or "")]
    ok_indexed = bool(table_clauses)
    log(f"[smoke]   served clauses={len(served)}  carrying the fee table (48,000): {len(table_clauses)}  "
        f"(atomic table span retrievable: {ok_indexed})")

    # 4b. END-TO-END with a capable answer model (the GENERAL/granite substrate over-abstains even on a direct
    # prose hit -- a separate, known generation-model limit, memory `generation-nondeterministic-abstain`; it is
    # orthogonal to whether the table is RETRIEVABLE, which is what 0014 fixes). Override via ISSUE0014_ANSWER_MODEL.
    answer_model_id = os.environ.get("ISSUE0014_ANSWER_MODEL", "qwen/qwen3.7-plus")
    log(f"[smoke]     (b) end-to-end with a capable answer model ({answer_model_id})")
    qa = production_intra_document_qa(store=store, top_k=12, answer_model_id=answer_model_id)

    async def ask(q: str):
        return (await qa.ainvoke({"contract_id": doc_id, "question": q}))["answer"]

    table_q = "What is the annual fee for the Enterprise tier?"
    control_q = "What is the cap on the Supplier's total aggregate liability?"
    table_a, control_a = await asyncio.gather(ask(table_q), ask(control_q))
    log(f"[smoke]   TABLE   Q: {table_q}")
    log(f"[smoke]           A: {table_a.answer[:160]!r}  (abstained={table_a.abstained}, "
        f"citations={len(table_a.citations)})")
    log(f"[smoke]   CONTROL Q: {control_q}")
    log(f"[smoke]           A: {control_a.answer[:160]!r}  (abstained={control_a.abstained}, "
        f"citations={len(control_a.citations)})")

    ok_table = (not table_a.abstained) and "48,000" in table_a.answer and bool(table_a.citations)
    ok_control = (not control_a.abstained) and bool(control_a.citations)  # general retrieval unregressed
    log(f"\n[smoke]   INDEXED (table in served evidence): {ok_indexed}   |   ANSWERED w/ 48,000 + cite: {ok_table}"
        f"   |   CONTROL (liability) answers: {ok_control}")
    store.close()

    if ok_indexed and ok_table and ok_control:
        log("[smoke]   PASS: the table figure is retrievable with a citation; nothing silently dropped (0014 fixed).")
        return 0
    log("[smoke]   FAIL: the table did not reach the evidence pool / did not answer (see above).")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
