"""EDGAR-derived relational + multi-hop golden set (T10, SPEC section 12 / section 8).

CUAD alone is single-document clause extraction; the entity graph exists to answer questions that
*traverse* it, and this is the golden set that measures that leg. Its ground truth is the
human-verified name->CIK set at `data/edgar/verification_set.json` (T10 verification; the
`resolution` fields are ground truth, never regenerated over). Building the answer key by fuzzy
matching would be circular (linking parties to CIKs *is* the entity-resolution problem, FR-C.7), so
only human-verified matches enter here.

Two question shapes, both anchored on a hub (an entity signing >= 3 contracts) and both set-valued
so recall@k is measured exactly as for the CUAD archetypes:

- 1-hop: the hub's full set of direct co-parties.
- 2-hop: entities reachable from the hub through exactly one shared counterparty and not directly
  (a real chain of three distinct verified entities joined by two real contractual edges).

Two standing rules from the T10 review are enforced here:

- The axis is verified-vs-unverified, never public-vs-private. A CIK filer and a verified-PRIVATE
  entity are equally first-class answers; folding private counterparties out would under-measure the
  graph leg on exactly the entities that dominate this corpus.
- Distinct subsidiaries keep separate answer keys (ScanSource vs ScanSource Latin America); one's
  counterparties are never listed under the other. This falls out of keying identity on the human
  `resolution`, so variant spellings of one filer collapse while genuinely distinct nodes do not.

Honest eval property (recorded, not hidden): the corpus is **public-filer-centric and
multi-hop-modest** -- it is star-shaped (hubs with co-parties that do not further interconnect), so
genuine 2-hop chains are concentrated in a couple of small clusters. That is a property of the
corpus, surfaced by measurement, not an artifact of under-building the set.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from eval.harness import Archetype, GoldenQuestion

_PRIVATE = "PRIVATE"
_SKIP = "SKIP"

# Honest property recorded alongside the built set (see module docstring).
EVAL_PROPERTY = "public-filer-centric, multi-hop-modest"

# The built set lands here (source tree, not `data/`): it embeds human-verified answer keys that
# exist nowhere else once the local triage file is set aside, so it is committed, not gitignored.
SET_PATH = Path("eval/golden/relational/set.json")


class AnswerEntity(BaseModel):
    """One ground-truth answer entity: a verified CIK filer or a verified-PRIVATE node."""

    entity_key: str
    representative: str
    entity_id: Optional[str] = None  # canonical EDGAR CIK; None for a verified-PRIVATE entity
    private: bool = False

    @property
    def identity(self) -> str:
        """Stable answer identifier: the CIK for a filer, a namespaced key for a private node.

        This is the eval-set answer id, not an `EntityId` -- the identifier contract (T1) stays
        strict canonical-CIK-only, and the private node is mapped to its graph node id at eval time.
        """
        return self.entity_id if self.entity_id else f"{_PRIVATE}:{self.entity_key}"


class RelationalQuestion(BaseModel):
    """A relational (1-hop) or multi-hop (2-hop) golden question over the EDGAR entity graph."""

    qid: str
    hop_count: int  # 1 (direct co-parties) or 2 (through one shared counterparty)
    question: str
    anchor: AnswerEntity
    answer_entities: list[AnswerEntity]
    # The entity_id evidence path: one identity chain per answer (`[hub, answer]` at 1 hop,
    # `[hub, bridge, answer]` at 2 hops). The chunk_id evidence path is resolved at eval time, once
    # the corpus is ingested (the same deferral as CUAD `relevant_ids`).
    entity_paths: list[list[str]] = Field(default_factory=list)

    def to_golden(self) -> GoldenQuestion:
        """Project onto the harness `GoldenQuestion` so the relational split runs in the same eval."""
        return GoldenQuestion(
            qid=self.qid,
            source_doc_id=f"graph:{self.anchor.entity_key}",
            archetype=Archetype.RELATIONAL,
            question=self.question,
            category=None,
            answer_spans=[a.representative for a in self.answer_entities],
            relevant_ids={a.identity for a in self.answer_entities},
        )


def _identity(entity: dict) -> str:
    resolution = entity["resolution"]
    if resolution in (_PRIVATE, _SKIP):
        return f"{_PRIVATE}:{entity['entity_key']}"
    return resolution


def _answer_entity(entity: dict) -> AnswerEntity:
    resolution = entity["resolution"]
    return AnswerEntity(
        entity_key=entity["entity_key"],
        representative=entity["representative"],
        entity_id=None if resolution in (_PRIVATE, _SKIP) else resolution,
        private=resolution == _PRIVATE,
    )


def build_relational(vset: dict) -> list[RelationalQuestion]:
    """Build the relational + multi-hop questions from a verified set (SKIP entities excluded)."""
    entities = [e for e in vset["entities"] if e["resolution"] != _SKIP]
    by_key = {e["entity_key"]: e for e in entities}

    # One canonical answer entity per resolved identity; first occurrence wins, so variant spellings
    # of a filer (same CIK) collapse to a single answer entity.
    canonical: dict[str, AnswerEntity] = {}
    for entity in entities:
        canonical.setdefault(_identity(entity), _answer_entity(entity))

    # Undirected co-party graph keyed by identity; self/variant edges drop out (ic != ie).
    adj: dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        ie = _identity(entity)
        for coparty_key in entity["coparty_keys"]:
            coparty = by_key.get(coparty_key)
            if coparty is None:
                continue  # a co-party outside the verified set is not a ground-truth answer
            ic = _identity(coparty)
            if ic != ie:
                adj[ie].add(ic)
                adj[ic].add(ie)

    # Hubs = entities signing >= 3 contracts; dedupe hub variants by identity, deterministically.
    hubs = [e for e in entities if e["num_contracts"] >= 3 or e.get("is_top_hub")]
    seen: set[str] = set()
    hub_order: list[dict] = []
    for entity in sorted(hubs, key=lambda e: (-e["num_contracts"], e["entity_key"])):
        identity = _identity(entity)
        if identity not in seen:
            seen.add(identity)
            hub_order.append(entity)

    questions: list[RelationalQuestion] = []
    for hub in hub_order:
        hub_id = _identity(hub)
        anchor = canonical[hub_id]
        direct = sorted(adj[hub_id])

        if direct:
            questions.append(
                RelationalQuestion(
                    qid=f"rel:1hop:{hub['entity_key']}",
                    hop_count=1,
                    question=f"Which parties contract directly with {anchor.representative}?",
                    anchor=anchor,
                    answer_entities=[canonical[i] for i in direct],
                    entity_paths=[[hub_id, i] for i in direct],
                )
            )

        # 2-hop: a target reachable through a shared counterparty, not itself a direct co-party.
        bridge_of: dict[str, str] = {}  # target identity -> a witnessing bridge (first, deterministic)
        for bridge in direct:
            for target in sorted(adj[bridge]):
                if target != hub_id and target not in adj[hub_id] and target not in bridge_of:
                    bridge_of[target] = bridge
        if bridge_of:
            targets = sorted(bridge_of)
            questions.append(
                RelationalQuestion(
                    qid=f"rel:2hop:{hub['entity_key']}",
                    hop_count=2,
                    question=(
                        f"Which parties are connected to {anchor.representative} through exactly one "
                        "shared counterparty (two contractual hops), but do not contract with it directly?"
                    ),
                    anchor=anchor,
                    answer_entities=[canonical[t] for t in targets],
                    entity_paths=[[hub_id, bridge_of[t], t] for t in targets],
                )
            )

    return questions


def build_and_write(vset_path: Path = Path("data/edgar/verification_set.json")) -> dict:
    """Build the relational set from the verified set and write it to `SET_PATH` with metadata."""
    vset = json.loads(vset_path.read_text())
    questions = build_relational(vset)
    payload = {
        "eval_property": EVAL_PROPERTY,
        "counts": {
            "one_hop": sum(1 for q in questions if q.hop_count == 1),
            "two_hop": sum(1 for q in questions if q.hop_count == 2),
        },
        "questions": [q.model_dump() for q in questions],
    }
    SET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SET_PATH.write_text(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    result = build_and_write()
    print(f"wrote {SET_PATH}: {result['counts']} ({result['eval_property']})")
