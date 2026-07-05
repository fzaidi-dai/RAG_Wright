#!/usr/bin/env python
"""Build the CUAD-annotation golden set for the ingested subset (T9). Output: data/eval/golden.json.

Reproducible: the output record pins the CUAD source snapshot (from the subset manifest) and the
subset's selected contract ids, so the same snapshot + subset reproduce the same golden set. The
golden data is gitignored (a generated eval artifact, and cleaner on the CC-BY front); it is rebuilt
from the committed builder + the pinned corpus on demand.

    uv run python -m eval.build_golden
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from eval.golden import build_golden

CUAD_SQUAD = Path("data/cuad/extracted/CUAD_v1.json")
SUBSET_MANIFEST = Path("data/cuad/subset/manifest.json")
OUT = Path("data/eval/golden.json")


def main() -> None:
    squad = json.loads(CUAD_SQUAD.read_text())["data"]
    manifest = json.loads(SUBSET_MANIFEST.read_text())
    stems = {sid.lower() for sid in manifest["selected_ids"]}
    include = {c["title"] for c in squad if c["title"].lower() in stems}
    golden = build_golden(squad, include_docs=include)

    record = {
        "source_snapshot": manifest["source_snapshot"],  # pins CUAD (e.g. zenodo:4595826)
        "subset_selected_ids": sorted(manifest["selected_ids"]),  # pins the subset
        "archetype_map": "T9 hypothesis (revisit once per-category/per-leg recall is observable)",
        "num_questions": len(golden),
        "questions": [q.model_dump(mode="json") for q in golden],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2))

    dist = collections.Counter(q.archetype.value for q in golden)
    print(f"golden set: {len(golden)} questions over {len(include)} subset contracts")
    for archetype, n in sorted(dist.items()):
        print(f"  {archetype:15}: {n}")
    print(f"pinned: source={record['source_snapshot']}, subset={len(record['subset_selected_ids'])} contracts")
    print(f"written: {OUT} (gitignored)")


if __name__ == "__main__":
    main()
