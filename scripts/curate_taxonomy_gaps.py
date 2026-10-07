"""INGEST-LLM-CLASSIFIER (ADR-0048) step 2: LLM-assisted taxonomy-gap curation (human-overseen).

Takes the out-of-taxonomy clause types the LLM found (data/eval/taxonomy_gaps/gap_report.json) and, with the LLM,
sorts each into: FOLD (a synonym/variant of one of the 44 existing labels -> give that label), DROP (a
heading/artifact/fragment, not a real clause function), or ADD (a genuine new clause-function type our taxonomy
lacks -> a clean canonical name + one-line definition, clustering synonyms to the SAME name). Emits a PROPOSAL
(curation_proposal.json + a readable report) for human review -- it does NOT modify the taxonomy.

  MIN_COUNT=3 uv run --no-sync python -m scripts.curate_taxonomy_gaps
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

from dotenv import load_dotenv
from pydantic import BaseModel


class Decision(BaseModel):
    term: str
    verdict: str          # FOLD | DROP | ADD
    target: str           # FOLD: the existing label; ADD: proposed canonical new label; DROP: ""
    definition: str = ""  # ADD only: a one-line definition of the new clause type


class Curation(BaseModel):
    decisions: list[Decision]


def log(m: str) -> None:
    print(m, flush=True)


_PROMPT = (
    "You are curating a legal clause-function TAXONOMY. Here are the {n_existing} EXISTING function labels:\n{existing}\n\n"
    "Below are candidate clause types an LLM found in real contracts that did NOT match the existing labels, with "
    "how many clauses had each. For EACH term decide ONE verdict:\n"
    "- FOLD: it is a synonym/spelling/casing variant of an EXISTING label -> set `target` to that exact existing label.\n"
    "- DROP: ONLY a pure structural artifact -- a section number, 'Signature', 'Section Heading', a bare word like "
    "'Definition' or 'Acknowledgment'. If a term names a REAL legal function (e.g. 'Limitation Of Liability', "
    "'Royalty Payment', 'Right of First Refusal'), it is NEVER a DROP -- FOLD it if an existing label fits, else ADD.\n"
    "- ADD: it is a GENUINE clause-function type the taxonomy lacks -> set `target` to a clean canonical Title-Case "
    "name. CLUSTER AGGRESSIVELY: all payment/fee/expense/compensation terms -> ONE name; all royalty terms -> 'Royalties'; "
    "all collateral/security-interest terms -> ONE name. Reuse the SAME `target` across every synonym. Give a one-line "
    "`definition`.\nBe globally consistent: the same concept must get the same verdict and the same target everywhere.\n"
    "Return one decision per term.\n\nCandidate terms (term | count):\n{terms}"
)


def main() -> None:
    os.environ.setdefault("RAG_SERVING", "openrouter")
    os.environ.pop("OPENROUTER_PROVIDER", None)
    load_dotenv()
    from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS, canonical_function
    from rag_wright.models.profiles import ModelRole, model_for
    from rag_wright.models.seam import build_structured

    min_count = int(os.environ.get("MIN_COUNT", "3"))
    report = json.load(open("data/eval/taxonomy_gaps/gap_report.json"))
    gaps = [(t, c) for t, c in report["out_of_taxonomy"].items() if c >= min_count]
    log(f"[curate] {len(gaps)} out-of-taxonomy terms with count>={min_count} (of {len(report['out_of_taxonomy'])})")

    model = model_for(ModelRole.STRUCTURED_REASONING)  # a reasoning model for the taxonomy judgement
    runnable = build_structured(model, Curation)
    existing = "\n".join(FUNCTION_LABELS)

    decisions: list[Decision] = []
    batch = int(os.environ.get("BATCH", "45"))  # BATCH>=len(gaps) => ONE global call (consistent clustering)
    for i in range(0, len(gaps), batch):
        chunk = gaps[i:i + batch]
        terms = "\n".join(f"{t} | {c}" for t, c in chunk)
        prompt = _PROMPT.format(n_existing=len(FUNCTION_LABELS), existing=existing, terms=terms)
        try:
            out = runnable.invoke(prompt)
            decisions.extend(out.decisions)
        except Exception as e:  # noqa: BLE001
            log(f"[curate] batch {i // batch} failed: {str(e)[:80]}")
        log(f"[curate] curated {min(i + batch, len(gaps))}/{len(gaps)} terms")

    counts = {t: c for t, c in report["out_of_taxonomy"].items()}
    fold: dict[str, str] = {}
    drop: list[str] = []
    add_terms: dict[str, list[str]] = defaultdict(list)   # proposed new label -> supporting terms
    add_freq: Counter = Counter()
    add_def: dict[str, str] = {}
    for d in decisions:
        c = counts.get(d.term, 0)
        v = d.verdict.strip().upper()
        if v == "FOLD" and canonical_function(d.target):
            fold[d.term] = canonical_function(d.target)
        elif v == "ADD" and d.target.strip():
            name = d.target.strip()
            add_terms[name].append(d.term)
            add_freq[name] += c
            if d.definition.strip() and name not in add_def:
                add_def[name] = d.definition.strip()
        else:
            drop.append(d.term)

    proposal = {
        "min_count": min_count,
        "add_new_labels": [
            {"label": name, "clauses": add_freq[name], "definition": add_def.get(name, ""),
             "from_terms": add_terms[name]}
            for name, _ in add_freq.most_common()],
        "fold_aliases": [{"alias": t, "into": lbl, "clauses": counts.get(t, 0)}
                         for t, lbl in sorted(fold.items(), key=lambda kv: -counts.get(kv[0], 0))],
        "drop": sorted(drop, key=lambda t: -counts.get(t, 0)),
    }
    out_path = "data/eval/taxonomy_gaps/curation_proposal.json"
    json.dump(proposal, open(out_path, "w"), indent=2, ensure_ascii=False)

    log("\n=== CURATION PROPOSAL (for human review; nothing applied) ===")
    log(f"ADD {len(proposal['add_new_labels'])} new labels | FOLD {len(fold)} aliases | DROP {len(drop)}")
    log("\nPROPOSED NEW LABELS (label | clauses | definition):")
    for a in proposal["add_new_labels"][:40]:
        log(f"  {a['clauses']:5d}  {a['label']:32s} {a['definition'][:70]}")
    log("\nTOP FOLD ALIASES (alias -> existing label):")
    for f in proposal["fold_aliases"][:20]:
        log(f"  {f['clauses']:5d}  {f['alias']!r} -> {f['into']!r}")
    log(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
