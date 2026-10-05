"""RAG_Wright quickstart — open a workspace, ingest a document, ask a cited question.

Everything goes through `rag_wright.api`; nothing reaches the store, embedder, or a model id.

Prerequisites (see docs/installation.md):
  - ArcadeDB running locally, with ARCADEDB_* set in a .env at the repo root.
  - A model provider: OPENROUTER_API_KEY in .env (default), or RAG_SERVING=vllm + VLLM_BASE_URL/VLLM_API_KEY.

Run from the repo root:
    uv run python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from dotenv import load_dotenv

from rag_wright.api import (
    EngineConfig,
    StoreConfig,
    ainvoke_subgraph,
    kg_read,
    load_reference_pack,
    measure_usage,
    open_workspace,
    source_document,
)

# The engine ships an EMPTY capability catalog; opt into the contract/compliance reference pack so its
# ingestion/retrieval subgraphs are registered. (A real product registers its own domain capabilities instead.)
load_reference_pack()

CONTRACT = (
    "Section 3. Fees. Customer shall pay the fees set forth in each Order Form within thirty (30) days of the "
    "invoice date.\n\n"
    "Section 7. Confidentiality. Each party shall protect the other party's Confidential Information using no less "
    "than reasonable care and shall not disclose it to any third party without prior written consent.\n\n"
    "Section 8. Limitation of Liability. Except for breaches of confidentiality or a party's indemnification "
    "obligations, in no event shall either party's aggregate liability arising out of or relating to this Agreement "
    "exceed the total fees paid by Customer to Provider in the twelve (12) months preceding the event giving rise to "
    "the claim. In no event shall either party be liable for any indirect, incidental, or consequential damages.\n\n"
    "Section 10. Term and Termination. This Agreement commences on the Effective Date and continues for an initial "
    "term of two (2) years. Either party may terminate for material breach upon thirty (30) days' written notice if "
    "the breach remains uncured.\n\n"
    "Section 12. Governing Law. This Agreement shall be governed by the laws of the State of New York, without "
    "regard to its conflict-of-laws principles."
)


async def main() -> None:
    load_dotenv()  # ARCADEDB_* + the provider key from .env

    config = EngineConfig(
        store=StoreConfig(
            host=os.environ.get("ARCADEDB_HOST", "localhost"),
            port=os.environ.get("ARCADEDB_PORT", "2480"),
            user=os.environ.get("ARCADEDB_USER", "root"),
            password=os.environ["ARCADEDB_PASSWORD"],
            protocol=os.environ.get("ARCADEDB_PROTOCOL", "http"),
        )
    )
    ws = open_workspace(config, corpus="quickstart_demo", reset=True)  # fresh demo DB each run

    with measure_usage() as usage:
        # 1) Ingest: chunk -> segment -> classify -> extract -> embed -> write the typed clause KG.
        doc = source_document("ACME_MSA", text=CONTRACT)
        with tempfile.TemporaryDirectory() as cache:
            await ainvoke_subgraph(
                "contract_ingestion_pipeline", {"document": doc, "cache_dir": cache}, resources=ws
            )

        # 2) The KG is populated: one typed clause per provision, each carrying its operative-span anchor.
        clauses = kg_read(ws, "Clause", fields=["clause_id", "function"])
        print(f"ingested {len(clauses)} provision clause(s): {[c['function'] for c in clauses]}")

        # 3) Ask a question scoped to that document -> a grounded, CITED answer (or an abstention).
        out = await ainvoke_subgraph(
            "intra_document_qa",
            {"contract_id": "ACME_MSA", "question": "What is the liability cap?"},
            resources=ws,
        )

    answer = out["answer"]
    print("\n=== ANSWER ===")
    print(answer.answer)
    print("cited spans:", answer.citations)
    print(f"\n=== USAGE === {usage.calls} model call(s), ${usage.cost_usd:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
