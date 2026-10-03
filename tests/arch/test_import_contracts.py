"""DD-6 (ADR-0067 #5, AC-no-leak): enforce the de-domaining import boundary with import-linter, IN the test
suite (no separate CI). The GENERIC engine foundation (the store + the de-domained generic primitives) must not
import the reference CONTRACT/COMPLIANCE domain pack -- a re-coupling import fails the committed contract in
`[tool.importlinter]`. A second test proves the linter actually DETECTS a violation, so the first is not vacuous.
"""
from __future__ import annotations

from importlinter.configuration import configure
from importlinter.application.use_cases import UserOptions, create_report, lint_imports

configure()  # initialize import-linter's app settings (TIMER etc.) -- the CLI does this; the programmatic API needs it


def test_de_domaining_import_contract_holds():
    # runs the committed [tool.importlinter] contract(s) against the real graph; True == all kept
    assert lint_imports(config_filename="pyproject.toml", no_logo=True) is True


def test_import_linter_actually_detects_a_violation():
    # RED proof (so the green above is not vacuous): a forbidden contract that is GENUINELY violated by the code
    # -- store.arcadedb really does import ontology.loader (load_kg_schema) -- must be reported broken.
    report = create_report(UserOptions(
        session_options={"root_packages": ["rag_wright"]},
        contracts_options=[{
            "name": "proof: store.arcadedb must not import ontology.loader (deliberately violated)",
            "type": "forbidden",
            "source_modules": ["rag_wright.store.arcadedb"],
            "forbidden_modules": ["rag_wright.ontology.loader"],
        }],
    ))
    checks = report.get_contracts_and_checks()
    assert checks and not all(check.kept for _, check in checks)  # the deliberate violation is caught
