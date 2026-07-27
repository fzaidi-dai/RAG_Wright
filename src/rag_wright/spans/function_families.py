"""Step-5b: confusable-clause FAMILIES for the LLM-hybrid classifier. Derived data-drivenly from the
LegalBERT model's holdout confusion (>=0.12 of a class's gold spans predicted as the sibling; connected
components). `ROUTE_FAMILIES` is the dev-validated subset where an LLM (Gemma) beats LegalBERT at the
sibling distinction -- only these route to the LLM; the rest keep LegalBERT (which is stronger on them, e.g.
Cap-vs-Uncapped, which Gemma craters). Labels are canonical `FUNCTION_LABELS`; membership is canonicalized
so it matches the classifier's CUAD-cased output too."""

from __future__ import annotations

from rag_wright.contracts.function import canonical_function

# All confusable families (from the confusion graph). Kept for reference / diagnostics.
CONFUSABLE_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("Affiliate License-Licensee", "Competitive Restriction Exception", "Exclusivity",
     "Irrevocable Or Perpetual License", "License Grant", "Non-Transferable License"),
    ("Anti-Assignment", "Change Of Control"),
    ("Effective Date", "Expiration Date"),
    ("Unlimited/All-You-Can-Eat-License", "Volume Restriction"),
    ("Affiliate License-Licensor", "IP Ownership Assignment", "Joint IP Ownership"),
    ("Notice Period To Terminate Renewal", "Price Restrictions", "Renewal Term", "Termination For Convenience"),
    ("Most Favored Nation", "No-Solicit Of Customers", "Non-Compete"),
    ("Cap On Liability", "Uncapped Liability"),
    ("Liquidated Damages", "Revenue/Profit Sharing"),
    ("Indemnification", "No-Solicit Of Employees", "Third Party Beneficiary"),
)

# The dev-validated LLM-winning subset: route ONLY these to Gemma (test-half mean non-NONE recall
# 0.627 -> 0.641; fixes Notice-Period 0->0.33, Irrevocable 0->0.50, No-Solicit 0.17->0.50). The excluded
# families (liability, control, dates, ...) keep LegalBERT, which is stronger there.
ROUTE_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("Affiliate License-Licensee", "Competitive Restriction Exception", "Exclusivity",
     "Irrevocable Or Perpetual License", "License Grant", "Non-Transferable License"),
    ("Affiliate License-Licensor", "IP Ownership Assignment", "Joint IP Ownership"),
    ("Notice Period To Terminate Renewal", "Price Restrictions", "Renewal Term", "Termination For Convenience"),
    ("Most Favored Nation", "No-Solicit Of Customers", "Non-Compete"),
    ("Liquidated Damages", "Revenue/Profit Sharing"),
)

# The rare, low-recall classes (all in a ROUTE family) worth rescuing with the LLM. TARGETED routing fires
# only when one of these is in the LegalBERT top-2 -- so the common/strong classes never route (zero risk to
# them), and only a fraction of a percent of spans hit the LLM. These went from ~0 to 0.33-0.67 with routing.
RARE_TARGETS: frozenset[str] = frozenset({
    "Irrevocable Or Perpetual License", "Notice Period To Terminate Renewal", "No-Solicit Of Customers",
    "Non-Transferable License", "Competitive Restriction Exception", "Price Restrictions",
    "Affiliate License-Licensee",
})

for _fam in CONFUSABLE_FAMILIES:  # canonical-guard the labels at import
    assert all(canonical_function(c) for c in _fam), f"non-canonical label in family {_fam}"
assert all(canonical_function(c) for c in RARE_TARGETS), "non-canonical label in RARE_TARGETS"

_ROUTE_OF: dict[str, tuple[str, ...]] = {
    canonical_function(c): tuple(canonical_function(x) for x in fam)
    for fam in ROUTE_FAMILIES
    for c in fam
}


def route_family(label: str) -> tuple[str, ...] | None:
    """The route-family (canonical labels) that `label` belongs to, or None if `label` is not in a routed
    family (case-insensitive: the classifier's CUAD-cased output maps in too). None => keep LegalBERT."""
    return _ROUTE_OF.get(canonical_function(label or ""))
