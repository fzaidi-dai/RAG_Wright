"""The third axis, as a RATCHET: deployment choice, vendor names and tenancy shape (T-7.58, engine PS-32).

**This is the guard the ENGINE asked us to send with the handover**, and it will run over
`rag_wright.harness` in their suite from adoption onward, beside their import contracts. It exists because
their review of ADR-0139 found an axis both of our earlier measurements were blind to: a module can name no
domain word and import nothing from the product and still carry our deployment choice, a vendor's name, or
our tenancy shape. `checkpoints.py`, `models.py` and `memory.py` all passed both of our tests and none of
them was generic.

**A ratchet, not a wall**, following `tests/test_engine_api_boundary.py`. 1 module violates the axis today
-- it was 6 until stage 3 moved `checkpoints.py` out of the harness, emptying the deployment category --
and the separation burns the rest down one stage at a time; the sets below may only ever SHRINK. A guard that
demanded purity today would have to be disabled today, and a disabled guard is worse than an honest one.
This one fails the moment a NEW violation appears.

The measurement itself is `scripts/measure_harness_genericity.py`, committed for the same reason this is:
we told the engine "three imports" when it was 13, they wrote our figures into their ADR without their own
walk, and we then could not reproduce our own second figure because the stem list died with the session.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PACKAGE = ROOT / "src" / "rag_wright" / "harness"

#: **Only ever removed from.** Each entry is a module that carries something the substrate must not, with
#: what replaces it. The engine has already stated the target shape for all three kinds.
KNOWN_VENDOR = {
    # **Shrunk, not cleared, at T-7.58 stage 2.** `DEFAULT_AGENT_MODEL`, `is_openrouter()`, two hand-written
    # `extra_body` dialects, the provider pin and the `ProviderProfile` registration are all gone -- the
    # engine builds the model now (PS-36). What is left is ONE trace-metadata key,
    # `"openrouter_generation_id"`, and it is deliberately still here: the same key is written by
    # `packs/contracts/capabilities/dg_extraction.py`, so renaming it in the substrate alone would split one
    # concept across two key names in Langfuse, which is worse than the vendor word. Retiring it is a trace
    # SCHEMA change across both writers and any saved query that reads it.
    "models.py": 'the trace metadata key "openrouter_generation_id", shared with the contracts pack',
}
#: **EMPTIED at T-7.58 stage 3.** `checkpoints.py` was never substrate: nothing in the harness imported it,
#: its only production consumer is `api/routes/chat.py`, and every line of it is the product's deployment
#: half -- our SQLAlchemy-to-psycopg URL conversion, our pool settings, our dedicated schema. It was
#: product code that happened to live in the substrate's directory, so it MOVED to
#: `rulewright/run_storage.py` rather than being redesigned. The engine's shape is met by construction:
#: the substrate accepts a checkpointer, the product builds the Postgres one, and the engine needs no
#: Postgres extra.
KNOWN_DEPLOYMENT: dict[str, str] = {}
#: **EMPTY, and this axis is CLOSED** (2026-10-13, T-7.58). All five originals are gone. `composition.py`
#: was product glue nothing imported; `accounting.py` took the callbacks the engine prescribed; `runs.py`
#: lost its pair as a CONSEQUENCE of accounting's seam; `tools.py` LEFT the harness at 4c-iv because it IS
#: the product's tool surface; and `context.py` left last.
#:
#: **`context.py` was the sharpest of them and was INVISIBLE until the measurement was fixed.**
#: `AgentContext` DECLARES `customer_id` and `workspace_id` as dataclass fields, so the substrate's central
#: type -- the one passed to `create_deep_agent` as `context_schema` -- encoded this product's two-level
#: tenancy, and a product with one level or three could not fill it. The guard matched call shapes only, so
#: it scored zero on the most central instance of what it checks for. Raised with the engine, which chose
#: the `RunScope` protocol (ADR-0139 addendum): the substrate reads `scope` and `shared_scope` and never
#: names a product's context type, and `AgentContext` now lives under `rulewright/agent/` where it belongs.
#:
#: **Empty is a WALL, not a list.** Anything added here is a regression and needs its reason in the commit.
KNOWN_TENANCY: dict[str, str] = {}


#: Substrate modules that import a product DOMAIN package, and what cuts each edge. **Added at stage 4c-ii
#: because that stage moved coupling rather than removing it**: splitting `workers.py` cleared it from that
#: module and `tiers.py` + `decisions.py` immediately picked the same dependency up under a new name
#: (`rulewright.agent.workers`). That is a legitimate intermediate step -- the edge is now explicit at the
#: two places that must receive the roster instead of buried inside a module -- but nothing failed when the
#: count went from four to five, and a separation whose numbers can rise silently is one nobody is holding.
#: **EMPTY.** Every substrate module that reached a product domain package has been dealt with:
#: `tools.py` and `manifest.py` moved (4c-iv), `continuation.py` moved whole, `workers.py` split,
#: `decisions.py` is told what kind of tier it observes, and `tiers.py` lost its `_definition` default --
#: which was the last one, and was a dependency wearing a convenience.
#:
#: **The test stays, and that is the point of an empty ratchet**: it is now a wall. The next substrate
#: module that imports `rulewright.operations`, `.workbench`, `.tenancy`, `.packs` or `.agent` fails here
#: with nowhere to be excused to.
KNOWN_DOMAIN_IMPORTERS: dict[str, str] = {}


def test_THE_SUBSTRATES_DOMAIN_COUPLING_ONLY_EVER_SHRINKS() -> None:
    """**The guard that would have caught 4c-ii moving coupling instead of cutting it.** Axis 1 is
    measured on every run but nothing asserted it, so the count rose from four to five in silence."""
    rows = {r["name"]: r for r in _measure()["modules"]}
    importers = {name for name, r in rows.items() if r["domain_imports"]}

    unexpected = sorted(importers - set(KNOWN_DOMAIN_IMPORTERS))
    assert not unexpected, (
        f"new substrate modules import a product domain package: {unexpected}. Take what you need as an "
        f"argument -- the substrate cannot know what a clause is. Found: "
        f"{ {n: rows[n]['domain_imports'] for n in unexpected} }"
    )

    stale = sorted(set(KNOWN_DOMAIN_IMPORTERS) - importers - {n for n in KNOWN_DOMAIN_IMPORTERS if n not in rows})
    gone = sorted(n for n in KNOWN_DOMAIN_IMPORTERS if n not in rows)
    assert not stale and not gone, (
        f"stale exemptions -- remove them in the change that cut the edge: cleaned={stale}, "
        f"no longer harness modules={gone}"
    )


def _measure() -> dict:
    from measure_harness_genericity import measure
    return measure(PACKAGE)


def test_THE_MEASUREMENT_IS_NOT_VACUOUS() -> None:
    """A measurement over an empty set reports perfect genericity. Check it found the package first."""
    rows = _measure()["modules"]

    assert len(rows) >= 25, f"expected the harness's ~29 modules, measured {len(rows)}"
    assert sum(r["lines"] for r in rows) > 5000, "the line count is too small to be the real harness"
    assert not any(r["imports"] for r in rows), (
        f"the harness imports engine internals or product code: "
        f"{ {r['name']: sorted(r['imports']) for r in rows if r['imports']} }. Reach the engine through "
        f"`rag_wright.api` only (ADR-0139)."
    )


def test_THE_IMPORT_WALK_SEES_A_PLANTED_INTERNAL_IMPORT(tmp_path) -> None:
    """**Plant-verified**: an empty import column above means a clean harness only if the walk can see one."""
    from measure_harness_genericity import measure

    pkg = tmp_path / "src" / "rag_wright" / "harness"
    pkg.mkdir(parents=True)
    (pkg / "planted.py").write_text("from rag_wright.store import arcadedb\nfrom rag_wright import api\n")
    (row,) = measure(pkg)["modules"]
    assert row["imports"] == {"rag_wright.store"}


def test_NO_NEW_MODULE_NAMES_A_VENDOR_OR_A_MODEL() -> None:
    """Choosing the model is configuration, through the engine's model-profile seam (engine ADR-0139)."""
    offenders = {r["name"]: r["vendors"] for r in _measure()["modules"] if r["vendors"]}

    unexpected = sorted(set(offenders) - set(KNOWN_VENDOR))
    assert not unexpected, (
        f"new modules name a vendor or a model in CODE: {unexpected}. The substrate takes a chat model or "
        f"a profile name and never names either. Use `agent_chat_model` (engine PS-36). Found: "
        f"{ {k: offenders[k] for k in unexpected} }"
    )


def test_NO_NEW_MODULE_MAKES_A_DEPLOYMENT_CHOICE() -> None:
    """**A driver import IS a deployment choice.** The substrate accepts any LangGraph checkpointer and the
    product constructs the Postgres one, which is why the engine needs no Postgres extra."""
    offenders = {r["name"]: r["deployment"] for r in _measure()["modules"] if r["deployment"]}

    unexpected = sorted(set(offenders) - set(KNOWN_DEPLOYMENT))
    assert not unexpected, (
        f"new modules import a database driver or a cloud SDK: {unexpected}. Accept the built object from "
        f"the caller instead. Found: { {k: offenders[k] for k in unexpected} }"
    )


def test_NO_NEW_MODULE_CARRIES_OUR_TENANCY_SHAPE() -> None:
    """`(customer, workspace)` is OUR pair, and the most likely of the three to look generic and not be:
    the substrate takes a namespace prefix from the caller and appends its own parts."""
    offenders = {r["name"]: r["tenancy"] for r in _measure()["modules"] if r["tenancy"]}

    unexpected = sorted(set(offenders) - set(KNOWN_TENANCY))
    assert not unexpected, (
        f"new modules hardcode the (customer, workspace) pair: {unexpected}. Take a namespace prefix as an "
        f"argument. Found: { {k: offenders[k] for k in unexpected} }"
    )


def test_THE_RATCHET_ONLY_EVER_SHRINKS() -> None:
    """**The half that makes it a ratchet.** The tests above catch a NEW violation; this one catches a
    stale exemption -- a module listed as violating that no longer does, which is a stage finished and not
    recorded. Without it the known sets would quietly become permanent."""
    rows = {r["name"]: r for r in _measure()["modules"]}

    stale: list[str] = []
    for exemptions, axis, why in ((KNOWN_VENDOR, "vendors", "no longer names a vendor"),
                                  (KNOWN_DEPLOYMENT, "deployment", "no longer imports a driver"),
                                  (KNOWN_TENANCY, "tenancy", "no longer carries the tenancy pair")):
        for name in exemptions:
            # **A module that LEFT the harness is stale too, and the first version of this missed it.**
            # The check was `if name in rows and not rows[name][axis]`, which skips a module that no longer
            # exists -- so stage 4 moved `composition.py` out and its exemption sat here unnoticed, which
            # is the precise failure a ratchet is supposed to make impossible. A separation REMOVES modules;
            # that is its whole mechanism, so "gone" is the common case, not the edge one.
            if name not in rows:
                stale.append(f"{name} is no longer a harness module at all")
            elif not rows[name][axis]:
                stale.append(f"{name} {why}")

    assert not stale, (
        "these exemptions are now stale -- remove them from this file in the same change that cleaned the "
        f"module, or the ratchet stops ratcheting: {stale}"
    )


def test_THE_AMBIGUOUS_STEMS_ARE_DECLARED_SEPARATELY() -> None:
    """Axis 2 reports a BAND, not a number, because six terms are real domain words here and ordinary
    words in a generic harness (a retry `policy`, a `requirement` of a task). A single figure that
    silently included or excluded them is the kind of number this whole thread was spent correcting."""
    from measure_harness_genericity import AMBIGUOUS_STEMS, DOMAIN_STEMS

    assert not (DOMAIN_STEMS & AMBIGUOUS_STEMS), (
        f"a stem is in both lists, so the band is incoherent: {sorted(DOMAIN_STEMS & AMBIGUOUS_STEMS)}"
    )
    assert AMBIGUOUS_STEMS, "the ambiguous list is empty, so axis 2 is reporting a point estimate again"


#: **The product the harness came from, as TEXT** (PS-43). The axes above read code; the handover left the
#: source's prose speaking that product: its name, its task and requirement ids, its ADR numbers (which
#: collide with the engine's own, so `ADR-0019` here pointed a reader at the wrong decision), its context
#: type, its example scope and its tool names. Docstrings are what `docs/api/harness.md` and
#: `docs/harness.md` are generated from, so prose here is public text. A wall, not a ratchet.
_PRODUCT_TEXT = (
    r"rulewright",
    r"\bT-\d+[a-z]?\.\d",                      # the product's task ids
    r"\b(?:AR|ANFR|NFR)-\d+",                  # the product's requirement ids
    r"\bAgentContext\b", r"\bfor_tenant\b", r"\bacme\b",
    r"\b(?:the|a|one|this|that|another|each|every|whole|same)\s+matter\b", r"\bmatter's\b",
    r"\bmatters\b(?=\s*(?:\)|,|\.|$))",       # "a customer's matters" (the noun, not the verb)
    r"[/_-]matter\b|\bmatter[/_-]",
    r"\bcustomer_id\b", r"\bworkspace_id\b",
    r"\bcompliance_worker\b", r"\bask_contract_question\b", r"\bparty_exposure\b", r"list my contracts",
    r"§ ?\d", r"\bADP-\d", r"run-contract\.md", r"\bSPEC\.md\b",   # the product's spec and its sections
    r"(?<!harness/)\btests/test_",            # the product's test files (the engine's are tests/harness/)
)
#: The engine ADRs the harness may cite. Any other number is the product's, or a citation nobody checked.
_ENGINE_ADRS = {"ADR-0125", "ADR-0139"}


def _product_text(text: str) -> list[str]:
    import re

    pattern = re.compile("|".join(_PRODUCT_TEXT), re.IGNORECASE)
    hits = [line.strip() for line in text.splitlines() if pattern.search(line)]
    hits += [line.strip() for line in text.splitlines()
             if set(re.findall(r"ADR-\d+", line)) - _ENGINE_ADRS]
    return hits


def test_THE_SUBSTRATE_SPEAKS_NO_PRODUCTS_LANGUAGE() -> None:
    offenders = {path.name: hits for path in sorted(PACKAGE.glob("*.py"))
                 if (hits := _product_text(path.read_text(encoding="utf-8")))}
    assert offenders == {}, f"product vocabulary in the harness source: {offenders}"


def test_THE_PRODUCT_TEXT_GUARD_SEES_A_PLANTED_LEAK() -> None:
    assert _product_text('"""Bound to that run\'s matter (T-5a.4c-iii, ADR-0019)."""')
    assert _product_text("#: Narrowest first: `(\"acme\", \"matter-7\")`.")
    assert not _product_text("#: **Trailing slashes matter**: no matter what (engine ADR-0139).")
