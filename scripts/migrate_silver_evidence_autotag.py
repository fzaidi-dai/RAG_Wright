"""PREC-1a (a): reformat the frozen silver fixture's evidence text to the new auto-tag framing, in place.

The silver eval runs on the FROZEN evidence_snapshot.json (built on the Modal KG). PREC-1a (a) changed how the
pipeline frames a served clause -- from the asserted prefix "Cap On Liability: <text>" to the to-verify tag
"[auto-tag: Cap On Liability] <text>". To keep the frozen eval measuring the CURRENT pipeline, this reformats
the label prefix on the SAME content (same clauses, ids, facts, and hand-authored key), rather than
regenerating (which would risk drifting to different contracts on the local KG, breaking the key).

Deterministic + idempotent (re-running is a no-op). It only rewrites the leading function prefix produced by
`intra_document_qa._clause_to_evidence`; body, typed facts, the ADR-0044 exception frame, and the key are
untouched.

  uv run --no-sync python -m scripts.migrate_silver_evidence_autotag
"""

from __future__ import annotations

import json
from pathlib import Path

from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS

_PATH = Path("tests/fixtures/leg_a_silver/evidence_snapshot.json")
_EXC = "[Exception to the liability cap (inferred)] "  # ADR-0044 frame, kept verbatim
_FNS = sorted(FUNCTION_LABELS, key=len, reverse=True)  # longest-first so multi-word labels win


def _reframe(text: str) -> str:
    prefix = ""
    if text.startswith(_EXC):
        prefix, text = _EXC, text[len(_EXC):]
    if text.startswith("[auto-tag:"):
        return prefix + text  # already migrated -> idempotent
    for fn in _FNS:
        if text.startswith(f"{fn}: "):
            return f"{prefix}[auto-tag: {fn}] {text[len(fn) + 2:]}"
        if text.startswith(f"{fn} — "):  # em-dash: the property-less facts path
            return f"{prefix}[auto-tag: {fn}] {text[len(fn) + 3:]}"
        if text == fn:
            return f"{prefix}[auto-tag: {fn}]"
    raise ValueError(f"could not reframe (no known function prefix): {text[:70]!r}")


def main() -> None:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    n = 0
    for rec in data["records"]:
        for ev in rec["evidence"]:
            new = _reframe(ev["text"])
            if new != ev["text"]:
                ev["text"] = new
                n += 1
    _PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[migrate] reframed {n} evidence items -> {_PATH}", flush=True)


if __name__ == "__main__":
    main()
