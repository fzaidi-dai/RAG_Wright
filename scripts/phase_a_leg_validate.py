"""MCP-PROTO Phase A: validate the query-leg SUBGRAPHS end-to-end against the Modal KG + A100, no MCP.

Runs the REGISTERED leg subgraphs (not the direct-helper shortcuts adoption_query_validate.py used for A/C) so
any KG/model integration issue surfaces before MCP is layered on. Same substrate as Leg B's validation: the
Modal ArcadeDB KG (`ragwright_cuad_full`, https) + the A100 models (vLLM-Granite via the seam, BGE/LegalBERT via
the STACK_URL adapters).

  ARCADEDB_HOST=<rw-arcadedb>.modal.run ARCADEDB_PORT=443 ARCADEDB_PROTOCOL=https \
    ARCADEDB_USER=root ARCADEDB_PASSWORD=rag_wright_dev_2026 ARCADEDB_DATABASE=ragwright_cuad_full \
    RAG_SERVING=vllm VLLM_BASE_URL=<a100>/v1 VLLM_API_KEY=rw-vllm-dev-key STACK_URL=<a100> \
    uv run --no-sync python -m scripts.phase_a_leg_validate A

Arg selects the leg(s): A (intra_document_qa). C-rel and C-cross are added as their bindings land (A2/A3).
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv


def _line(s: str = "") -> None:
    print(s, flush=True)


def validate_leg_a() -> None:
    """A1: intra_document_qa -- (contract_id, question) -> cited GeneratedAnswer, all on the Modal stack."""
    from rag_wright.capabilities.answer_generator import SeamAnswerModel
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.store.arcadedb import ArcadeDBStore
    from rag_wright.subgraphs.intra_document_qa import production_intra_document_qa

    store = ArcadeDBStore.from_env(database="ragwright_cuad_full")
    llm_id = model_for(ModelRole.GENERAL)  # granite via the seam (RAG_SERVING=vllm -> A100)
    answer_model = SeamAnswerModel(llm_id)
    leg_a = production_intra_document_qa(store=store, answer_model=answer_model, function_model_id=llm_id)

    # pick a real contract that has a well-known clause function (the Modal KG is 506, so never the 4 recovered)
    row = store._query("SELECT clause_id FROM Clause WHERE function = 'Cap On Liability' LIMIT 1")
    if not row:
        _line("  [A1] no 'Cap On Liability' clause found in the KG -- cannot pick a test contract")
        store.close()
        return
    contract_id = row[0]["clause_id"].rsplit(":", 2)[0]
    question = "How is liability capped in this contract, and under what conditions?"
    _line("=" * 90)
    _line(f"LEG A -- intra_document_qa SUBGRAPH | contract={contract_id} | Q={question!r}")
    out = leg_a.invoke({"contract_id": contract_id, "question": question})
    ans = out["answer"]
    _line(f"  abstained: {ans.abstained}")
    _line(f"  citations ({len(ans.citations)}): {[c[-16:] for c in ans.citations[:6]]}")
    _line(f"  answer: {ans.answer.strip()[:400]}")
    _line(f"  [A1] {'OK -- cited answer' if (not ans.abstained and ans.citations) else 'CHECK -- abstained/uncited'}")
    store.close()


_LEGS = {"A": validate_leg_a}


def main() -> None:
    load_dotenv()
    which = sys.argv[1] if len(sys.argv) > 1 else "A"
    for key in ([which] if which in _LEGS else list(_LEGS)):
        _LEGS[key]()


if __name__ == "__main__":
    main()
