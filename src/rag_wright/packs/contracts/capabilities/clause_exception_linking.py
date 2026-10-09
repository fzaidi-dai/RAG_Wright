"""ADR-0044: the `IS_EXCEPTION_TO` derived carve-out relationship (exception clause -> the Cap clause it excepts).

A single contract's liability structure is usually stored as TWO disconnected clauses -- a `Cap On Liability`
clause (often without its carve-outs) and a separate, often property-less `Uncapped Liability` clause (the
carve-out, e.g. "uncapped for negligence") -- with NO relationship between them. So a query like "how is
liability capped, and under what conditions?" gets two contradictory-looking fragments and the generator
abstains, instead of "capped at X, EXCEPT uncapped for negligence."

This capability adds the missing link WITHOUT re-ingesting: a pure pass over the already-populated clauses (a derived-relationship
linking pass over the existing KG) that writes `IsExceptionTo` edges. The signal is SYMBOLIC co-occurrence +
POSITIONAL PROXIMITY: within a contract, an `Uncapped` clause whose operative span is within `window` characters
of a `Cap` clause's span (~ the same liability section) is that cap's carve-out. Distant co-occurrence is NOT
linked (probably an unrelated standalone uncapped clause). The link is **INFERRED** (a reasoned inference, not an
extracted fact, FR-S.4): surfaced at query time and human-validatable, never a silent hard claim.

Scope (ADR-0044): the edge type is general; only the cap<->uncapped pair is populated for now.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from rag_wright.pack_sdk import CapabilityRegistry
from rag_wright.pack_sdk import ConfidenceTag

CAP_FUNCTION = "Cap On Liability"
EXCEPTION_FUNCTION = "Uncapped Liability"
DEFAULT_PROXIMITY_WINDOW = 3000  # chars between the two spans' ranges (~ one liability section); tunable


class ClauseExceptionLink(BaseModel):
    """One `IsExceptionTo` edge: the `exception_clause_id` (an Uncapped clause) is a carve-out/exception to the
    `cap_clause_id` (a Cap clause) in `contract_id`. Confidence INFERRED -- a derived, reasoned link (FR-S.4)."""

    exception_clause_id: str
    cap_clause_id: str
    contract_id: str
    confidence: ConfidenceTag = ConfidenceTag.INFERRED


class ClauseExceptionLinkResult(BaseModel):
    """The capability's output: the derived `IsExceptionTo` links + the honest unlinked count."""

    links: list[ClauseExceptionLink]
    unlinked_exceptions: int  # Uncapped clauses with no in-window Cap clause (skipped, not linked)
    contracts_processed: int


def _gap(a: dict, b: dict) -> int:
    """Character gap between two spans' [doc_start, doc_end] ranges (0 if they overlap)."""
    a0, a1 = int(a.get("doc_start") or 0), int(a.get("doc_end") or 0)
    b0, b1 = int(b.get("doc_start") or 0), int(b.get("doc_end") or 0)
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0


def derive_exception_links(
    positions: list[dict], *, window: int = DEFAULT_PROXIMITY_WINDOW,
    cap_function: str = CAP_FUNCTION, exception_function: str = EXCEPTION_FUNCTION,
) -> ClauseExceptionLinkResult:
    """Pure: within each contract, link each `exception_function` clause to the NEAREST `cap_function` clause
    whose operative span is within `window` chars (proximity ~ same section) -> an INFERRED `IsExceptionTo` link.
    A distant or cap-less exception clause is counted `unlinked`, never linked to a far cap (that would be a
    false carve-out). One link per (exception, cap) pair. `positions`: rows with {clause_id, function,
    contract_id, doc_start, doc_end}."""
    by_contract: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"cap": [], "exc": []})
    for p in positions:
        bucket = by_contract[p["contract_id"]]
        if p["function"] == cap_function:
            bucket["cap"].append(p)
        elif p["function"] == exception_function:
            bucket["exc"].append(p)

    links: list[ClauseExceptionLink] = []
    unlinked = 0
    for contract_id, bucket in by_contract.items():
        caps = bucket["cap"]
        if not caps:  # uncapped clauses but no cap clause in this contract -> nothing to except
            unlinked += len(bucket["exc"])
            continue
        for exc in bucket["exc"]:
            nearest = min(caps, key=lambda cap: _gap(exc, cap))
            if _gap(exc, nearest) <= window:
                links.append(ClauseExceptionLink(
                    exception_clause_id=exc["clause_id"], cap_clause_id=nearest["clause_id"],
                    contract_id=contract_id))
            else:
                unlinked += 1  # co-occurs but distant -> probably unrelated; do not link (no false carve-out)

    return ClauseExceptionLinkResult(
        links=links, unlinked_exceptions=unlinked, contracts_processed=len(by_contract))


def clause_exception_linking(store: Any, *, window: int = DEFAULT_PROXIMITY_WINDOW) -> ClauseExceptionLinkResult:
    """The registered capability (ADR-0044): read the Cap + Uncapped clause positions, derive the proximity-based
    `IsExceptionTo` links, write them (idempotent, clears the layer first), and return the result. No re-ingest --
    a derived-relationship pass over the existing KG. `store` is the contracts pack's `ContractKGStore` (ING-8e:
    the clause reads/writes live there, not on the generic store)."""
    positions = store.clause_positions([CAP_FUNCTION, EXCEPTION_FUNCTION])
    result = derive_exception_links(positions, window=window)
    store.write_clause_exception_links(result.links)
    return result


def register_clause_exception_linking(registry: CapabilityRegistry) -> None:
    """ADR-0044: register `clause_exception_linking` (function; the cap<->uncapped carve-out relationship)."""
    registry.register(
        "clause_exception_linking",
        contract=ClauseExceptionLinkResult,
        kind="function",
        display_name="Clause exception linking (IsExceptionTo carve-out edges over the contract KG)",
    )
