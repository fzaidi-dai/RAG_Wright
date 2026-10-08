"""Generate docs/api/README.md from the LIVE public API symbols (PREP-3.1).

The API reference is GENERATED from `rag_wright.api.__all__` (signatures + docstrings via `inspect`), never
hand-written, so it cannot drift from the code. CI regenerates and diffs (`scripts/build_api_docs.sh`); a stale
commit fails. Markdown (diffable); a hosted site (pdoc/mkdocs) is a later extension.

Run: `uv run python scripts/build_api_docs.py`  (or `bash scripts/build_api_docs.sh`).
"""
from __future__ import annotations

import enum
import inspect
from pathlib import Path

import rag_wright.api as api

OUT = Path(__file__).resolve().parent.parent / "docs" / "api" / "README.md"


def _summary(obj: object) -> str:
    """The whole docstring, each paragraph whitespace-collapsed (paragraph breaks kept)."""
    doc = inspect.getdoc(obj) or ""
    if not doc:
        return "_(no docstring)_"
    return "\n\n".join(" ".join(para.split()) for para in doc.split("\n\n"))


def _own_methods(cls: type) -> list[tuple[str, object]]:
    """The public methods a class defines itself (not inherited, not dataclass/pydantic machinery)."""
    out = []
    for mname, member in vars(cls).items():
        if mname.startswith("_") or not inspect.isfunction(member):
            continue
        out.append((mname, member))
    return out


def _is_protocol(obj: object) -> bool:
    return inspect.isclass(obj) and bool(getattr(obj, "_is_protocol", False))


def _signature(name: str, obj: object) -> str:
    if _is_protocol(obj):  # a hook protocol: show the call it requires, not the Protocol constructor
        params = list(inspect.signature(obj.__call__).parameters.values())[1:]  # drop `self`
        sig = inspect.signature(obj.__call__).replace(parameters=params)
        return f"{name}: {sig}"
    try:
        return f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return name


def render() -> str:
    """The reference's full text, from the live `rag_wright.api.__all__` (the drift test compares it to the file)."""
    lines: list[str] = [
        "# API reference — `rag_wright.api`",
        "",
        "> **Generated** from the live `rag_wright.api.__all__` by `scripts/build_api_docs.py` — do not edit by "
        "hand. Regenerate with `bash scripts/build_api_docs.sh`; the test suite fails if it drifts (`tests/arch/test_api_docs_current.py`). The whole public "
        "surface is imported from `rag_wright.api`.",
        "",
    ]
    classes: list[str] = []
    protocols: list[str] = []
    aliases: list[str] = []
    constants: list[str] = []
    functions: list[str] = []
    for name in api.__all__:
        obj = getattr(api, name)
        if getattr(obj, "__module__", "") == "typing" or getattr(obj, "__origin__", None) is not None:
            aliases.append(f"### `{name} = {obj!r}`\n")  # a typing alias (Literal / Callable): show its definition
            continue
        if not inspect.isclass(obj) and not callable(obj):
            constants.append(f"### `{name}`\n\n{_summary(type(obj))}\n")  # a sentinel/constant: its type's doc
            continue
        block = [f"### `{_signature(name, obj)}`", "", _summary(obj), ""]
        if inspect.isclass(obj) and issubclass(obj, enum.Enum):  # an enum: list its members (what a caller passes)
            block += ["Members: " + ", ".join(f"`{m.name}` (`{m.value!r}`)" for m in obj), ""]
        if inspect.isclass(obj) and not _is_protocol(obj):
            for mname, member in _own_methods(obj):
                block += [f"#### `{name}.{mname}{inspect.signature(member)}`", "", _summary(member), ""]
        bucket = protocols if _is_protocol(obj) else classes if inspect.isclass(obj) else functions
        bucket.append("\n".join(block))

    lines += ["## Types", ""] + classes
    lines += ["## Hook protocols", "", "Callables you pass to the engine; any function with this signature conforms.",
              ""] + protocols
    lines += ["## Type aliases", ""] + aliases
    if constants:
        lines += ["## Constants", ""] + constants
    lines += ["## Functions", ""] + functions
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT} ({len(api.__all__)} public symbols)")


if __name__ == "__main__":
    main()
