"""Engine issue 0002 / ADR-0054: remove the `[auto-tag: TYPE]` prefix from the frozen silver fixture's evidence
text, in place -- the reverse of `migrate_silver_evidence_autotag.py` (PREC-1a).

The silver eval runs on the FROZEN evidence_snapshot.json (built on the Modal KG). ADR-0054 stopped
`intra_document_qa._clause_to_evidence` from putting the KG function label in the evidence text (it was
generation-only, not load-bearing for the judge-by-text method, and the source of the auto-tag paraphrase
leak). To keep the frozen eval measuring the CURRENT pipeline, this reformats the SAME content (same clauses,
ids, facts, exception frame, and hand-authored key) rather than regenerating (which would risk drifting to
different contracts on the local KG, breaking the key).

Deterministic + idempotent (re-running is a no-op). It only removes the leading `[auto-tag: TYPE]` prefix
produced by the old `_clause_to_evidence`; the ADR-0044 exception frame, the body, and the typed facts are kept.
A facts-only item (the label was its only prose alongside bare facts) is bracketed to match the new format; a
would-be contentless item (label only) is dropped, mirroring the new pipeline (returns None).

  uv run --no-sync python -m scripts.migrate_silver_evidence_remove_autotag
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PATH = Path("tests/fixtures/leg_a_silver/evidence_snapshot.json")
_EXC = "[Exception to the liability cap (inferred)] "  # ADR-0044 frame, kept verbatim
_AUTOTAG_RE = re.compile(r"^\[auto-tag: [^\]]*\]\s*")
_BARE_FACTS_RE = re.compile(r"^[a-z_]+=[^\[\]]*$")  # a facts-only remainder (dim=value; ...), no brackets


def _strip(text: str) -> str | None:
    """Remove the `[auto-tag: TYPE]` prefix (after any exception frame); return the new text, or None if the
    item becomes contentless (label only) and should be dropped. Idempotent: already-migrated text is unchanged."""
    prefix = ""
    if text.startswith(_EXC):
        prefix, text = _EXC, text[len(_EXC):]
    m = _AUTOTAG_RE.match(text)
    if m:
        text = text[m.end():].strip()
    if not text:
        return None  # label was the only content -> drop (new pipeline returns None)
    if _BARE_FACTS_RE.match(text):
        text = f"[{text}]"  # facts-only path -> bracketed, matching the new evidence format
    return prefix + text


def main() -> None:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    stripped = dropped = 0
    for rec in data["records"]:
        kept = []
        for ev in rec["evidence"]:
            new = _strip(ev["text"])
            if new is None:
                dropped += 1
                continue
            if new != ev["text"]:
                stripped += 1
            ev["text"] = new
            kept.append(ev)
        rec["evidence"] = kept
    _PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[migrate] removed auto-tag from {stripped} evidence items, dropped {dropped} contentless -> {_PATH}",
          flush=True)


if __name__ == "__main__":
    main()
