"""Refresh the generated tables in docs/harness.md from the live `rag_wright.harness` (PS-43).

`docs/harness.md` is the harness tier's design reference. Its prose is written; everything countable in it (the
field tables, the protocol members, the entry-point signatures, `worker_spec`'s defaults, the counts) is read off
the installed tier, between `<!-- generated:NAME -->` and `<!-- /generated -->` markers, so a field added to
`AgentDefinition` cannot leave the reference behind. The field descriptions are the `#:` comments in
`harness/definition.py`. Adapted from RuleWright's `draft_harness_design_doc.py`, which drafted the document.

Run: `uv run python scripts/build_harness_doc.py` (also run by `bash scripts/build_api_docs.sh`). Drift guard:
`tests/arch/test_api_docs_current.py`.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path

import rag_wright.harness as h

DOC = Path(__file__).resolve().parent.parent / "docs" / "harness.md"
_BLOCK = re.compile(r"(<!-- generated:(\w+) -->\n)(.*?)(<!-- /generated -->)", re.DOTALL)
_ADDRESS = re.compile(r"<(?:function|built-in function|class) ([\w.]+)(?: at 0x[0-9a-f]+)?>")


def _cell(text: str) -> str:
    """One table cell: whitespace collapsed, pipes escaped (a bare `|` in `str | None` splits the column)."""
    return " ".join((text or "").split()).replace("|", r"\|")


def _field_notes(cls: type) -> dict[str, str]:
    """`{field: its `#:` comment}`, read from the class's own source."""
    notes: dict[str, str] = {}
    pending: list[str] = []
    for line in inspect.getsource(cls).splitlines():
        stripped = line.strip()
        if stripped.startswith("#:"):
            pending.append(stripped[2:].strip())
            continue
        declared = re.match(r"^([a-z_][a-z0-9_]*)\s*:", stripped)
        if declared:
            notes[declared.group(1)] = " ".join(pending)
        if not stripped.startswith("#"):
            pending = []
    return notes


def _fields(cls: type) -> str:
    notes = _field_notes(cls)
    rows = []
    for f in dataclasses.fields(cls):
        note = notes.get(f.name, "")
        if not note:  # a field whose type says what it is: that type's own first line
            declared = getattr(h, str(f.type).strip("'\""), None)
            note = (inspect.getdoc(declared) or "").split("\n")[0] if declared is not None else ""
        rows.append(f"| `{f.name}` | `{_cell(str(f.type))}` | {_cell(note) or '—'} |")
    return "| field | type | what it is for |\n|---|---|---|\n" + "\n".join(rows)


def _protocol(name: str) -> str:
    proto = getattr(h, name)
    lines = [f"**`{name}`**: {_cell((inspect.getdoc(proto) or '').split(chr(10) * 2)[0])}", "",
             "| member | shape |", "|---|---|"]
    for member in sorted(proto.__protocol_attrs__):
        attr = getattr(proto, member, None)
        annotated = proto.__annotations__.get(member)
        if callable(attr):
            shape = f"`{_cell(_ADDRESS.sub(r'\1', str(inspect.signature(attr))))}`"
        elif annotated is not None:
            shape = f"`{_cell(str(annotated))}`"
        else:
            shape = "a property"
        lines.append(f"| `{member}` | {shape} |")
    return "\n".join(lines)


def _entry_points(*names: str) -> str:
    rows = []
    for name in names:
        obj = getattr(h, name)
        summary = (inspect.getdoc(obj) or "").split("\n\n")[0]
        rows.append(f"| `{name}` | {_cell(summary)} |")
    return "| name | what it does |\n|---|---|\n" + "\n".join(rows)


def _worker_spec_defaults() -> str:
    """`worker_spec`'s policy parameters, each with its inert default and where `definition=` takes it from."""
    declared = {f.name for f in dataclasses.fields(h.AgentDefinition)}
    rows = []
    for p in inspect.signature(h.worker_spec).parameters.values():
        if p.default is inspect.Parameter.empty or p.name == "definition":
            continue
        if p.name in declared:
            source = f"`definition.{p.name}`"
        elif p.name == "signposts":
            source = "`definition.prompts`"
        else:
            source = "not from the definition: the run's, passed down by the builder"
        rows.append(f"| `{p.name}` | `{p.default!r}` | {source} |")
    return ("| parameter | inert default | with `definition=`, taken from |\n|---|---|---|\n" + "\n".join(rows))


def _counts() -> str:
    return (f"{len(h.__all__)} public names; `AgentDefinition` has "
            f"{len(dataclasses.fields(h.AgentDefinition))} fields and `AgentPrompts` "
            f"{len(dataclasses.fields(h.AgentPrompts))}, every one required.")


BLOCKS = {
    "counts": _counts,
    "definition": lambda: _fields(h.AgentDefinition),
    "prompts": lambda: _fields(h.AgentPrompts),
    "tool_surface": lambda: _protocol("ToolSurface"),
    "run_scope": lambda: _protocol("RunScope"),
    "runs": lambda: _entry_points("start_run", "resume_run", "stream_run", "read_run"),
    "builders": lambda: _entry_points("build_chat_agent", "build_orchestrator", "worker_spec"),
    "worker_spec": _worker_spec_defaults,
}


def render(text: str) -> str:
    """`text` with every generated block refreshed; an unknown block name raises."""
    def fill(match: re.Match) -> str:
        return f"{match.group(1)}{BLOCKS[match.group(2)]()}\n{match.group(4)}"
    return _BLOCK.sub(fill, text)


def main() -> None:
    DOC.write_text(render(DOC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"refreshed {len(_BLOCK.findall(DOC.read_text(encoding='utf-8')))} generated blocks in {DOC}")


if __name__ == "__main__":
    main()
