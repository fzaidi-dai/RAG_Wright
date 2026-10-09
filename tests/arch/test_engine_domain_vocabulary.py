"""PS-R5a: the generic engine carries no domain vocabulary. The import contracts (`test_import_contracts.py`) stop
generic code from IMPORTING a pack; this stops domain KNOWLEDGE from leaking in as text -- prompts, skill files,
discovery text, public docstrings -- which the import-linter cannot see.

- Zero tolerance: the engine's skill files, the engine capability manifests' discovery text, and the docstrings of
  everything `rag_wright.api` exports.
- A ratchet everywhere else in the generic engine: a file's count of domain-term lines may only go down (the
  committed baseline), and a file not in the baseline must have none. Lower the baseline as files are cleaned
  (`uv run pytest tests/arch/test_engine_domain_vocabulary.py --update-baseline` is NOT offered on purpose: edit the
  JSON by hand, downward only).

The vocabulary is the reference contracts pack's: its clause-type labels plus legal terms. Bare "contract" is not
in it (it mostly means an API contract); "contract <domain noun>" is.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src" / "rag_wright"
_BASELINE = Path(__file__).with_name("domain_vocabulary_baseline.json")

# Allowed for now, each with the task that removes it (none: PS-R5c retired OKF, the last entry).
_SKILL_ALLOW: dict[str, str] = {}


def _pattern() -> re.Pattern:
    from rag_wright.packs.contracts.schemas.function import FUNCTION_LABELS

    terms = [r"\bclauses?\b", r"\bliabilit", r"\blawyers?\b", r"\blegal\b", r"\bindemnif", r"\bcarve-?outs?\b",
             r"\bcuad\b", r"\bacord\b", r"\bcounterpart(y|ies)\b",
             r"\bcontracts? (clause|text|evidence|page|chunk|part(y|ies)|terms?|documents?)\b"]
    labels = [re.escape(label) for label in FUNCTION_LABELS if len(label) > 6]
    return re.compile("|".join(terms + labels), re.IGNORECASE)


def _hits(text: str) -> list[str]:
    pat = _pattern()
    return [line.strip() for line in text.splitlines() if pat.search(line)]


def test_engine_skill_files_carry_no_domain_vocabulary():
    offenders = {}
    for skill in sorted((_SRC / "skills").glob("*/SKILL.md")):
        if skill.parent.name in _SKILL_ALLOW:
            continue
        hits = _hits(skill.read_text())
        if hits:
            offenders[skill.parent.name] = hits
    assert offenders == {}


def test_engine_capability_discovery_text_carries_no_domain_vocabulary():
    from rag_wright.capabilities.manifests import engine_capabilities

    offenders = {}
    for m in engine_capabilities():
        text = "\n".join([m.display_name or "", m.description or "", *(m.representative_queries or ())])
        hits = _hits(text)
        if hits:
            offenders[m.slug] = hits
    assert offenders == {}


def test_public_api_docstrings_carry_no_domain_vocabulary():
    from rag_wright import api

    offenders = {}
    for name in api.__all__:
        hits = _hits(inspect.getdoc(getattr(api, name)) or "")
        if hits:
            offenders[name] = hits
    for mod in sorted((_SRC / "api").glob("*.py")):
        doc = inspect.getdoc(__import__(f"rag_wright.api.{mod.stem}", fromlist=["_"])) or ""
        if _hits(doc):
            offenders[f"rag_wright.api.{mod.stem} (module)"] = _hits(doc)
    assert offenders == {}


def _generic_files() -> dict[str, int]:
    counts = {}
    for f in sorted(_SRC.rglob("*")):
        rel = f.relative_to(_SRC)
        if not f.is_file() or f.suffix not in (".py", ".md") or rel.parts[0] in ("packs", "skills"):
            continue
        n = len(_hits(f.read_text(errors="ignore")))
        if n:
            counts[str(rel)] = n
    return counts


def test_generic_engine_domain_vocabulary_only_goes_down():
    baseline = json.loads(_BASELINE.read_text())
    grew = {f: (n, baseline.get(f, 0)) for f, n in _generic_files().items() if n > baseline.get(f, 0)}
    assert grew == {}, f"domain vocabulary grew in generic engine files (now, baseline): {grew}"
