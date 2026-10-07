"""Build the CUAD-annotation golden sets (T9, SPEC section 12).

CUAD's SQuAD annotations give, per contract, one question per clause category with expert answer
spans (or `is_impossible` when the category is absent). Each positive `(contract, category)` becomes
a golden question, tagged with the archetype the category belongs to. The relational / multi-hop
archetype is built separately from the EDGAR graph (T10), not here.

`ARCHETYPE_BY_CATEGORY` is a curated partition of the 41 categories into the three CUAD-derivable
archetypes; it is a review decision (like the ontology seed), not something CUAD dictates.
"""

from __future__ import annotations

import re

from rag_wright.packs.contracts.schemas.ontology import ClauseCategory as C

from eval.harness import Archetype, GoldenQuestion

_CATEGORY_IN_QUESTION = re.compile(r'related to "([^"]+)"')

# Curated partition of the 41 CUAD clause categories into archetypes (review decision):
# - exact/lexical: a specific term/value a lexical (sparse/BM25) match finds (names, dates, law).
# - semantic: a conceptual restriction/obligation that needs meaning, not keywords.
# - clause-finding: locate a specific clause type (IP, license, liability, ...) and cite it.
#
# This map is a TESTABLE HYPOTHESIS, not ground truth: once per-category, per-leg recall is
# observable (Phase 5), a misplaced category will show up (e.g. a "semantic" category that only the
# sparse leg finds is really exact/lexical) and the map is revised. Known borderlines noted inline.
ARCHETYPE_BY_CATEGORY: dict[C, Archetype] = {
    # exact / lexical (8)
    C.DOCUMENT_NAME: Archetype.EXACT_LEXICAL,
    C.PARTIES: Archetype.EXACT_LEXICAL,  # borderline; lexical placement matches how it feeds entity extraction
    C.AGREEMENT_DATE: Archetype.EXACT_LEXICAL,
    C.EFFECTIVE_DATE: Archetype.EXACT_LEXICAL,
    C.EXPIRATION_DATE: Archetype.EXACT_LEXICAL,
    C.RENEWAL_TERM: Archetype.EXACT_LEXICAL,
    C.NOTICE_PERIOD_TO_TERMINATE_RENEWAL: Archetype.EXACT_LEXICAL,
    C.GOVERNING_LAW: Archetype.EXACT_LEXICAL,
    # semantic (11) -- kept genuinely meaning-dependent so semantic recall measures what it claims
    C.MOST_FAVORED_NATION: Archetype.SEMANTIC,
    C.NON_COMPETE: Archetype.SEMANTIC,
    C.EXCLUSIVITY: Archetype.SEMANTIC,
    C.NO_SOLICIT_OF_CUSTOMERS: Archetype.SEMANTIC,
    C.COMPETITIVE_RESTRICTION_EXCEPTION: Archetype.SEMANTIC,  # revisit candidate
    C.NO_SOLICIT_OF_EMPLOYEES: Archetype.SEMANTIC,
    C.NON_DISPARAGEMENT: Archetype.SEMANTIC,
    C.CHANGE_OF_CONTROL: Archetype.SEMANTIC,
    C.ANTI_ASSIGNMENT: Archetype.SEMANTIC,
    C.POST_TERMINATION_SERVICES: Archetype.SEMANTIC,  # revisit candidate
    C.COVENANT_NOT_TO_SUE: Archetype.SEMANTIC,
    # clause-finding (22)
    C.TERMINATION_FOR_CONVENIENCE: Archetype.CLAUSE_FINDING,  # phrase-anchored named clause: locate-and-cite
    C.ROFR_ROFO_ROFN: Archetype.CLAUSE_FINDING,
    C.REVENUE_PROFIT_SHARING: Archetype.CLAUSE_FINDING,
    C.PRICE_RESTRICTIONS: Archetype.CLAUSE_FINDING,
    C.MINIMUM_COMMITMENT: Archetype.CLAUSE_FINDING,
    C.VOLUME_RESTRICTION: Archetype.CLAUSE_FINDING,
    C.IP_OWNERSHIP_ASSIGNMENT: Archetype.CLAUSE_FINDING,
    C.JOINT_IP_OWNERSHIP: Archetype.CLAUSE_FINDING,
    C.LICENSE_GRANT: Archetype.CLAUSE_FINDING,
    C.NON_TRANSFERABLE_LICENSE: Archetype.CLAUSE_FINDING,
    C.AFFILIATE_LICENSE_LICENSOR: Archetype.CLAUSE_FINDING,
    C.AFFILIATE_LICENSE_LICENSEE: Archetype.CLAUSE_FINDING,
    C.UNLIMITED_ALL_YOU_CAN_EAT_LICENSE: Archetype.CLAUSE_FINDING,
    C.IRREVOCABLE_OR_PERPETUAL_LICENSE: Archetype.CLAUSE_FINDING,
    C.SOURCE_CODE_ESCROW: Archetype.CLAUSE_FINDING,
    C.AUDIT_RIGHTS: Archetype.CLAUSE_FINDING,
    C.UNCAPPED_LIABILITY: Archetype.CLAUSE_FINDING,
    C.CAP_ON_LIABILITY: Archetype.CLAUSE_FINDING,
    C.LIQUIDATED_DAMAGES: Archetype.CLAUSE_FINDING,
    C.WARRANTY_DURATION: Archetype.CLAUSE_FINDING,
    C.INSURANCE: Archetype.CLAUSE_FINDING,
    C.THIRD_PARTY_BENEFICIARY: Archetype.CLAUSE_FINDING,
}


def build_golden(
    squad_data: list[dict], *, include_docs: set[str] | None = None
) -> list[GoldenQuestion]:
    """Build golden questions from CUAD SQuAD `data`, one per positive `(contract, category)`.

    `include_docs` (contract titles) restricts the build to the ingested subset; `None` builds all.
    `is_impossible` / no-answer questions (the category is absent) are skipped.
    """
    by_name = {category.value.lower(): category for category in C}
    golden: list[GoldenQuestion] = []
    for contract in squad_data:
        title = contract["title"]
        if include_docs is not None and title not in include_docs:
            continue
        for paragraph in contract["paragraphs"]:
            for qa in paragraph["qas"]:
                if qa.get("is_impossible") or not qa["answers"]:
                    continue
                match = _CATEGORY_IN_QUESTION.search(qa["question"])
                if match is None:
                    continue
                category = by_name.get(match.group(1).strip().lower())
                if category is None:
                    continue
                golden.append(
                    GoldenQuestion(
                        qid=f"{title}::{category.name}",
                        source_doc_id=title,
                        archetype=ARCHETYPE_BY_CATEGORY[category],
                        question=qa["question"],
                        category=category,
                        answer_spans=[a["text"] for a in qa["answers"]],
                    )
                )
    return golden
