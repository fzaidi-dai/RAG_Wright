"""CLS-F/G live gate (FR-I.4, ADR-0115/0116): the full 29-dim best-of-both fleet, exercised through the real
classifier lane on realistic per-function clauses.

Live test — skips when the fleet checkpoints aren't present (they are gitignored / local-only, fetched from the
model store). It asserts the three properties the classifier-first Step-3a must hold:
  A. ON-FUNCTION FIRE   — each new CLS-F function's provision fires its target dim with a real (non-`none`) value,
                          under soft function-scoping (ADR-0116). The two corpus-starved dims (collateral_type,
                          escrow_release_trigger) currently ABSTAIN by design (data-starved, a documented CLS-F
                          future enhancement) — marked xfail so a later data-sourcing win surfaces as an xpass.
  B. ABSTAIN OFF-FUNCTION — each abstain dim, run unscoped on an off-topic (cap) clause, predicts `none` (the NONE
                          class from negatives), so a classifier that slips past scoping still says "not present".
  C. NO REGRESSION      — a cap clause through the scoped lane still fires the original dims and leaks no CLS-F dim.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from rag_wright.contracts.identifiers import ChunkId
from rag_wright.packs.contracts.spans.property_extractor import (
    HybridPropertyExtractor,
    Provenance,
    PropertyDimension as PD,
    scoped_dims,
)

_MODELS_DIR = Path(os.getenv("RAG_DIM_MODELS_DIR", "data/models"))
_FLEET_CFG = Path(__file__).resolve().parents[2] / "src" / "rag_wright" / "packs" / "contracts" / "spans" / "dim_fleet.json"


def _fleet_present() -> bool:
    """True only when EVERY model dir the fleet references has been fetched (laya + setfit)."""
    try:
        cfg = json.loads(_FLEET_CFG.read_text())
    except OSError:
        return False
    for spec in cfg.values():
        sub = "laya" if spec["framework"] == "laya" else "setfit"
        if not (_MODELS_DIR / sub / spec["model"]).exists():
            return False
    return True


pytestmark = [
    pytest.mark.fleet,  # loads the LOCAL 20+-model fleet (multi-GB RSS) -> opt-in, out of the default run
    pytest.mark.skipif(
        not _fleet_present(), reason="29-dim fleet checkpoints not present (gitignored / local-only)"),
]


ABSTAIN_DIMS = {
    "dispute_method": PD.DISPUTE_METHOD, "condition_type": PD.CONDITION_TYPE,
    "right_of_first_type": PD.RIGHT_OF_FIRST_TYPE, "collateral_type": PD.COLLATERAL_TYPE,
    "confidentiality_exception": PD.CONFIDENTIALITY_EXCEPTION, "royalty_basis": PD.ROYALTY_BASIS,
    "force_majeure_event": PD.FORCE_MAJEURE_EVENT, "escrow_release_trigger": PD.ESCROW_RELEASE_TRIGGER,
}

# One realistic provision per new CLS-F function: (function, target_dim, text).
_CLEAN = [
    ("Dispute Resolution", PD.DISPUTE_METHOD,
     "Any dispute, controversy or claim arising out of or relating to this Agreement shall be finally settled by "
     "binding arbitration administered by the American Arbitration Association under its Commercial Arbitration Rules."),
    ("Confidentiality", PD.CONFIDENTIALITY_EXCEPTION,
     "The obligations of confidentiality shall not apply to information that the receiving party can demonstrate was "
     "already in the public domain or becomes publicly available through no fault of the receiving party."),
    ("Royalties", PD.ROYALTY_BASIS,
     "Licensee shall pay Licensor a royalty equal to five percent (5%) of Net Sales of all Licensed Products sold "
     "during each calendar quarter, payable within thirty (30) days after the end of such quarter."),
    ("Condition Precedent", PD.CONDITION_TYPE,
     "The obligations of the parties to consummate the transactions contemplated hereby are subject to the "
     "satisfaction, at or prior to the Closing, of each of the following conditions: (a) the representations and "
     "warranties shall be true and correct; and (b) all required regulatory approvals shall have been obtained."),
    ("Force Majeure", PD.FORCE_MAJEURE_EVENT,
     "Neither party shall be liable for any failure or delay in performance caused by circumstances beyond its "
     "reasonable control, including acts of God, flood, fire, earthquake, or other natural disaster."),
    ("Rofr", PD.RIGHT_OF_FIRST_TYPE,
     "Before selling any of the Shares to a third party, the Selling Shareholder shall first offer such Shares to "
     "the Company on the same terms, and the Company shall have a right of first refusal to purchase them."),
    ("Rofn", PD.RIGHT_OF_FIRST_TYPE,
     "If the Owner intends to lease the Premises, the Owner shall first give the Tenant notice and the Tenant shall "
     "have a right of first negotiation to lease the Premises for thirty (30) days before any third-party offer."),
]
# Data-starved dims (cls-f-rare-values-starved): currently abstain on-function by design -> xfail (xpass = a win).
_STARVED = [
    pytest.param("Security Interest", PD.COLLATERAL_TYPE,
                 "As security for the Obligations, the Debtor grants to the Secured Party a continuing security "
                 "interest in all of the Debtor's equipment, machinery and fixtures, now owned or hereafter acquired.",
                 marks=pytest.mark.xfail(reason="collateral_type data-starved (CLS-F future enhancement)",
                                         strict=False)),
    pytest.param("Source Code Escrow", PD.ESCROW_RELEASE_TRIGGER,
                 "The Escrow Agent shall release the deposited source code to Licensee upon a Release Event, which "
                 "shall include the filing by Licensor of a petition in bankruptcy.",
                 marks=pytest.mark.xfail(reason="escrow_release_trigger data-starved (CLS-F future enhancement)",
                                         strict=False)),
]

_CAP_CLAUSE = (
    "In no event shall either party's aggregate liability arising out of or related to this Agreement exceed the "
    "total fees paid or payable by Customer to Provider during the twelve (12) months preceding the claim.")


def _cid(i: int) -> ChunkId:
    return ChunkId(source_doc_id="val_doc", chunk_index=i,
                   content_hash=hashlib.sha256(f"val_doc:{i}".encode()).hexdigest())


@pytest.fixture(scope="module")
def extractor():
    from rag_wright.packs.contracts.spans.dim_classifier import load_dim_registry

    reg = load_dim_registry()
    assert len(reg.dims) == 29, f"expected the full 29-dim fleet, got {len(reg.dims)}"
    # Classifier lane only: a stub runnable (never invoked by `_classifier_assertions`) avoids building an LLM client.
    return HybridPropertyExtractor(reg, runnable=object()), reg


@pytest.mark.parametrize("function,target,text", _CLEAN + _STARVED)
def test_new_dim_fires_on_function(extractor, function, target, text):
    ext, _ = extractor
    asserts = ext._classifier_assertions(Provenance.of(_cid(1)), text, "s", scoped_dims((function,)))
    value = next((a.value for a in asserts if a.dimension == target), None)
    assert value is not None and str(value).lower() != "none", (
        f"{function}: {target.value} did not fire (got {value!r}); emitted={[(a.dimension.value, a.value) for a in asserts]}")


@pytest.mark.parametrize("name,dim", list(ABSTAIN_DIMS.items()))
def test_abstain_dim_abstains_off_function(extractor, name, dim):
    _, reg = extractor
    preds = reg.get(dim).classify(_CAP_CLAUSE)
    top = preds[0][0] if preds else None
    assert str(top).lower() == "none", (
        f"{name} did not abstain off-function (top-1={top!r}); preds={[(str(v), round(float(p), 3)) for v, p in preds[:3]]}")


def test_no_regression_cap_clause_scoped(extractor):
    ext, _ = extractor
    scope = scoped_dims(("Cap On Liability",))
    asserts = ext._classifier_assertions(Provenance.of(_cid(99)), _CAP_CLAUSE, "s_cap", scope)
    got = {a.dimension for a in asserts}
    assert asserts, "cap clause produced zero assertions (regression)"
    leaked = got & set(ABSTAIN_DIMS.values())
    assert not leaked, f"CLS-F abstain dims leaked into the cap-clause scope: {[d.value for d in leaked]}"
    assert got <= scope, f"emitted dims outside the Cap On Liability scope: {[d.value for d in (got - scope)]}"
