"""ING-5: every engine-facing doc names only things that exist. A `rag_wright.<dotted>` symbol must import (module +
attributes), a repo file path must exist, a relative markdown link must resolve, and every Python snippet's engine
imports must resolve with every keyword argument it passes to an engine callable accepted by that callable's
signature. Historical records (ADRs, the archive, specs, proposals, eval write-ups, product hand-offs) are exempt:
they describe the code as it was."""
from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = sorted({
    _ROOT / "README.md", _ROOT / "CLAUDE.md",
    *(_ROOT / "docs").glob("*.md"),
    *(_ROOT / "docs" / "domain-adaptation").glob("*.md"),
    *(_ROOT / "docs" / "templates" / "product-starter").glob("*"),
    *(_ROOT / ".claude" / "skills").glob("*/SKILL.md"),
})
_DOTTED = re.compile(r"`[^`]*?\b(rag_wright(?:\.[A-Za-z_]\w*)+)")
_PATH = re.compile(r"`((?:[\w.-]+/)+[\w.-]+\.(?:py|ttl|md|json|sh|toml|template))(?::[\w.]+)?`")
_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
_PATH_ROOTS = ("", "src/rag_wright", "src")
_CODE = re.compile(r"```python\n(.*?)```", re.S)
# paths that live in another repository, named as such in the doc
_EXTERNAL = {("laya", "research/scripts/finetune_single_device.py"), ("laya", "docs/finetune.md")}


def _resolves(dotted: str) -> bool:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def _snippet_problems(code: str) -> list[str]:
    """Engine imports resolve; keyword args to an imported engine callable are in its signature."""
    try:  # a block may sit in a list item (indented) and use top-level `await` (a notebook-style snippet)
        tree = compile(textwrap.dedent(code), "<doc>", "exec", ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError:
        return []  # an illustrative fragment (placeholders like `<...>`), not runnable code
    out, imported = [], {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("rag_wright"):
            for alias in node.names:
                if not _resolves(f"{node.module}.{alias.name}"):
                    out.append(f"import of `{node.module}.{alias.name}`")
                else:
                    mod = importlib.import_module(node.module)
                    imported[alias.asname or alias.name] = getattr(mod, alias.name)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported):
            continue
        target = imported[node.func.id]
        try:
            params = inspect.signature(target).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind is p.VAR_KEYWORD for p in params.values()):
            continue
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                out.append(f"`{node.func.id}(... {kw.arg}=...)`: no such parameter")
    return out


def _path_exists(doc: Path, rel: str) -> bool:
    return any((_ROOT / base / rel).exists() for base in _PATH_ROOTS) or (doc.parent / rel).exists()


def _problems() -> list[str]:
    out: list[str] = []
    for doc in _DOCS:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        name = doc.relative_to(_ROOT)
        for dotted in sorted(set(_DOTTED.findall(text))):
            if not _resolves(dotted):
                out.append(f"{name}: unresolved symbol `{dotted}`")
        for rel in sorted(set(_PATH.findall(text))):
            if (doc.parent.name, rel) in _EXTERNAL:
                continue
            if "{" not in rel and not rel.startswith(("my_", "your_", "<")) and not _path_exists(doc, rel):
                out.append(f"{name}: missing path `{rel}`")
        for link in sorted(set(_LINK.findall(text))):
            if "://" in link or link.startswith(("mailto:", "{{")):
                continue
            if not (doc.parent / link).exists():
                out.append(f"{name}: broken link ({link})")
        for code in _CODE.findall(text):
            out += [f"{name}: snippet {p}" for p in _snippet_problems(code)]
    return out


def test_engine_docs_name_only_things_that_exist():
    problems = _problems()
    assert not problems, "stale doc references:\n" + "\n".join(problems)


def test_the_checker_catches_each_kind_of_stale_reference():
    # RED proof, so the green above is not vacuous
    assert not _resolves("rag_wright.api.id_source")  # a removed symbol
    assert _resolves("rag_wright.api.build_ingestion")
    bad = ("from rag_wright.api import build_ingestion, no_such_thing\n"
           "build_ingestion(lambda u: None, no_such_kwarg=1)\n")
    problems = _snippet_problems(bad)
    assert any("no_such_thing" in p for p in problems)
    assert any("no_such_kwarg" in p for p in problems)
    assert not _path_exists(_ROOT / "README.md", "src/rag_wright/spans/boundary.py")  # moved in ING-8c
