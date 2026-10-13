"""How generic is the agent harness? Three axes, committed so BOTH repos compute the same number.

**This exists because a measurement with no artifact is an anecdote.** We told the engine the harness had
"three imports from the product"; it was 13. They wrote our figures into ADR-0139 without their own walk.
We then corrected them and could not reproduce our own second figure, because the domain stem list lived in
a session that had been cleared. The engine will re-measure at handover (their PS-32 acceptance), and two
independent ad-hoc measurements of a fuzzy definition is how all of that happened.

So: one script, run from either repo, over any package.

    uv run python scripts/measure_harness_genericity.py [--package src/rag_wright/harness]

**In the engine (PS-32)** a "product import" is any import of a product package (`rulewright.*`) or of an engine
module other than the harness itself and its one door, `rag_wright.api`; the import contracts in `pyproject.toml`
enforce the same boundary. The domain packages are the engine's reference packs (`rag_wright.packs`).

**Axis 3 is the one the ENGINE runs** over `rag_wright.harness` in its own suite from adoption onward. It
is the axis both of our earlier tests were blind to: a module can name no domain word and import nothing
from the product and still carry our deployment choice, a vendor's name, or our tenancy shape. Their review
caught it; neither of our measurements could.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tokenize

# --- axis 2 vocabulary -------------------------------------------------------------------------------
#
# **Curated, not derived, and that is a deliberate reversal.** Deriving stems from the ontology (VOCAB
# keys, `Clause` fields, the closed value sets) yields 173 tokens including `action`, `basis`, `change`,
# `available` and `broad`. Those are generic English: a harness module using the word "change" would be
# counted domain-bound, so the derived list measures the wrong thing and in the dangerous direction -- it
# makes generic code look entangled and would justify leaving it behind.
#
# These are terms no general-purpose agent harness has any reason to contain.
DOMAIN_STEMS = frozenset({
    "arbitrat", "breach", "clause", "confidential", "contract", "counterpart", "exclusiv",
    "indemnit", "liabilit", "licensee", "licensor", "obligation", "renewal", "terminat",
})

# **Declared ambiguous, and counted BOTH ways rather than decided quietly.** Each is a real domain term
# here AND an ordinary word in a generic harness: a retry `policy`, a `requirement` of a task, a
# `document` the engine already owns, a `party` to a conversation, an `audit` log. A single number that
# silently included these would be indefensible, and excluding them without saying so would be worse.
AMBIGUOUS_STEMS = frozenset({"audit", "document", "matter", "party", "policy", "requirement"})

# --- axis 3: deployment choice, vendor names, tenancy shape ------------------------------------------
#
# Patterns, not a module list, so this covers the next violation rather than today's. Each cost a real
# finding in the engine's review of ADR-0139.
VENDOR_PATTERNS = (
    re.compile(r"openrouter", re.IGNORECASE),
    re.compile(r"\bopenai\b", re.IGNORECASE),
    re.compile(r"\bqwen\b", re.IGNORECASE),
    re.compile(r"\banthropic\b", re.IGNORECASE),
    re.compile(r"\bmodal\b", re.IGNORECASE),
)
# A driver import IS a deployment choice: the substrate must accept a checkpointer, not construct one.
DEPLOYMENT_IMPORTS = ("psycopg", "asyncpg", "sqlalchemy", "boto3", "google.cloud")
# Our tenancy shape, which looks generic and is not. `(customer, workspace)` is OUR pair.
#
# **The third pattern was missing and it was the most important one** (added 2026-10-13, found by
# `tests/test_greenfield_conformance.py`). The first two match CALL shapes -- a tuple, a keyword pair -- so
# `AgentContext`, which DECLARES `customer_id: uuid.UUID` and `workspace_id: uuid.UUID` as dataclass
# fields, scored zero tenancy hits. The substrate's central context type encoded this product's two-level
# tenancy and the guard said it was clean. A product with one level, or three, cannot fill that type.
#
# The engine runs this guard as a PS-32 acceptance check, so the hole was about to become theirs.
TENANCY_PATTERNS = (
    re.compile(r"\(\s*customer\s*,\s*workspace\s*\)"),
    re.compile(r"customer_id\s*,\s*workspace"),
    # A DECLARATION: `customer_id: <type>` / `workspace_id: <type>` in a class body.
    re.compile(r"\b(?:customer_id|workspace_id)\s*:\s*[A-Za-z_]"),
)


def _code_text(path: pathlib.Path) -> str:
    """The module's code with comments and docstrings removed.

    **Stripping both is load-bearing.** Half of this harness's prose explains the contract domain, and a
    docstring that says "clause" does not make the code domain-bound. An earlier version of this
    measurement that counted raw text would have reported almost every module as entangled.
    """
    out: list[str] = []
    try:
        with path.open("rb") as fh:
            prev = tokenize.INDENT
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.COMMENT:
                    continue
                if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                            tokenize.NL, tokenize.ENCODING):
                    prev = tok.type          # a bare string in statement position is a docstring
                    continue
                out.append(tok.string)
                if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                    prev = tok.type
    except (tokenize.TokenError, SyntaxError):
        return path.read_text(encoding="utf-8")
    return " ".join(out)


def _product_imports(tree: ast.AST, own_package: str) -> set[str]:
    hits: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.startswith(own_package) or name == "rag_wright" or name.startswith("rag_wright.api"):
                continue
            if name.startswith(("rulewright.", "rag_wright.")):
                hits.add(name)
    return hits


#: **`agent` was added at stage 4c-ii and the omission would have flattered us.** `rulewright.agent` is the
#: product's agent surface -- its workers name operations, its prompts name clauses and counterparties --
#: so a substrate module importing it is as coupled as one importing `operations`. Leaving it out would
#: have let the stage report `workers.py` cleared while `tiers.py` and `decisions.py` quietly picked up the
#: same dependency under a new name.
DOMAIN_PACKAGES = {"operations", "workbench", "tenancy", "packs", "agent"}


def measure(package: pathlib.Path) -> dict:
    modules = sorted(package.rglob("*.py"))
    own = f"{package.parent.name}.{package.name}" if package.parent.name else package.name
    rows = []
    for path in modules:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = _product_imports(tree, own_package=own)
        code = _code_text(path).lower()
        rows.append({
            "name": path.name,
            "lines": len(source.splitlines()),
            "imports": imports,
            "domain_imports": sorted(i for i in imports if i.split(".")[1] in DOMAIN_PACKAGES),
            "domain_stems": sorted(s for s in DOMAIN_STEMS if s in code),
            "ambiguous_stems": sorted(s for s in AMBIGUOUS_STEMS if s in code),
            "vendors": sorted({p.pattern for p in VENDOR_PATTERNS if p.search(code)}),
            "deployment": sorted({d for d in DEPLOYMENT_IMPORTS if any(i.startswith(d) for i in
                                  _all_imports(tree))}),
            "tenancy": [p.pattern for p in TENANCY_PATTERNS if p.search(code)],
        })
    return {"package": str(package), "modules": rows}


def _all_imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def report(result: dict) -> None:
    rows = result["modules"]
    n, lines = len(rows), sum(r["lines"] for r in rows)
    print(f"=== {result['package']}: {n} modules, {lines} lines ===\n")

    importers = [r for r in rows if r["imports"]]
    domain_importers = [r for r in rows if r["domain_imports"]]
    print("AXIS 1  product imports")
    print(f"  import product code      : {len(importers)}")
    print(f"  of those, a DOMAIN pkg   : {len(domain_importers)} "
          f"-> {', '.join(r['name'] for r in domain_importers)}")

    clean = [r for r in rows if not r["domain_stems"]]
    clean_strict = [r for r in rows if not r["domain_stems"] and not r["ambiguous_stems"]]
    print("\nAXIS 2  domain vocabulary (comments and docstrings stripped)")
    print(f"  name no domain concept   : {len(clean)}  (curated stems only)")
    print(f"  ... also no AMBIGUOUS term: {len(clean_strict)}  (the conservative reading)")
    print(f"  so the defensible figure is a BAND: {len(clean_strict)}-{len(clean)} of {n}")

    v = [r for r in rows if r["vendors"]]
    d = [r for r in rows if r["deployment"]]
    t = [r for r in rows if r["tenancy"]]
    print("\nAXIS 3  deployment, vendor, tenancy  <- the engine runs THIS one")
    print(f"  names a vendor           : {len(v)} -> {', '.join(r['name'] for r in v) or '-'}")
    print(f"  imports a driver/SDK     : {len(d)} -> {', '.join(r['name'] for r in d) or '-'}")
    print(f"  carries our tenancy shape: {len(t)} -> {', '.join(r['name'] for r in t) or '-'}")

    liftable = [r for r in rows if not r["imports"] and not r["domain_stems"]
                and not r["vendors"] and not r["deployment"] and not r["tenancy"]]
    work = [r for r in rows if r not in liftable]
    print("\nALL THREE AXES INTERSECTED")
    print(f"  lifts unchanged          : {len(liftable)} modules, {sum(r['lines'] for r in liftable)} lines")
    print(f"  needs work               : {len(work)} modules, {sum(r['lines'] for r in work)} lines")
    print("\n  **This is the honest size of the separation.** The earlier 12/2,193 split counted only")
    print("  axes 1 and 2, so it credited modules that carry a vendor name or a driver import as liftable.")


def main() -> int:
    arg = next((a for i, a in enumerate(sys.argv) if sys.argv[i - 1] == "--package"), None)
    package = pathlib.Path(arg or "src/rag_wright/harness")
    if not package.is_dir():
        print(f"no such package: {package}", file=sys.stderr)
        return 2
    report(measure(package))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
