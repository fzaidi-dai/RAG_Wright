"""Generate docs/api/README.md from the LIVE public API symbols (PREP-3.1).

The API reference is GENERATED from `rag_wright.api.__all__` (signatures + docstrings via `inspect`), never
hand-written, so it cannot drift from the code. CI regenerates and diffs (`scripts/build_api_docs.sh`); a stale
commit fails. Markdown (diffable); a hosted site (pdoc/mkdocs) is a later extension.

Run: `uv run python scripts/build_api_docs.py`  (or `bash scripts/build_api_docs.sh`).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import rag_wright.api as api

OUT = Path(__file__).resolve().parent.parent / "docs" / "api" / "README.md"


def _summary(obj: object) -> str:
    doc = inspect.getdoc(obj) or ""
    if not doc:
        return "_(no docstring)_"
    # first paragraph, whitespace-collapsed
    para = doc.split("\n\n", 1)[0]
    return " ".join(para.split())


def _signature(name: str, obj: object) -> str:
    try:
        return f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return name


def main() -> None:
    lines: list[str] = [
        "# API reference — `rag_wright.api`",
        "",
        "> **Generated** from the live `rag_wright.api.__all__` by `scripts/build_api_docs.py` — do not edit by "
        "hand. Regenerate with `bash scripts/build_api_docs.sh`; CI diffs it, so it cannot drift. The whole public "
        "surface is imported from `rag_wright.api`.",
        "",
    ]
    classes: list[str] = []
    functions: list[str] = []
    for name in api.__all__:
        obj = getattr(api, name)
        block = [f"### `{_signature(name, obj)}`", "", _summary(obj), ""]
        (classes if inspect.isclass(obj) else functions).append("\n".join(block))

    lines += ["## Types", ""] + classes
    lines += ["## Functions", ""] + functions
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(api.__all__)} public symbols)")


if __name__ == "__main__":
    main()
