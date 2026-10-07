"""ING-3b (ADR-0066): the contract pack's segmentation vocabulary lives in `contract_bridge.ttl`, not in Python. This
locks the move: the loaded vocabulary is exactly what the segmenter used before, and the patterns built from it decide
exactly as the original literal patterns did (an intentional vocabulary edit updates the pinned sets here)."""
from __future__ import annotations

import re

from rag_wright.packs.contracts.ontology.loader import load_segmentation_vocab
from rag_wright.packs.contracts.spans import segment

_ABBREV_BEFORE = {
    "inc", "corp", "co", "ltd", "llc", "llp", "plc", "no", "nos", "art", "sec", "secs", "para", "paras",
    "cf", "vs", "v", "mr", "mrs", "ms", "dr", "st", "ave", "etc", "al", "viz", "e.g", "i.e", "u.s", "u.s.c",
}
_FURNITURE_BEFORE = re.compile(
    r"^\s*(?:by|name|title|attest|witness|its|date|signature"
    r"|attention|attn|facsimile|fax|e-?mail|telephone|tel|phone)\s*:", re.IGNORECASE)
_SECTION_WORD_BEFORE = re.compile(r"^(?:§\s*|(?:section|article|clause|sec|art)\.?\s+)(?=\(?\d)", re.IGNORECASE)

_PROBES = [
    "By: /s/ John", "Name: Jane", "TITLE:", "Attest :", "Witness:", "Its: CEO", "Date: 1/1/2020", "Signature:",
    "Attention: Legal", "Attn: Legal", "Facsimile: 555", "Fax: 1", "Email: a@b.c", "E-mail: a@b.c", "e mail: x",
    "Telephone: 1", "Tel: 1", "Phone: 1", "By signing below the parties agree", "Bylaws: amended", "Dated: 2020",
    "Section 8. Governing Law", "SECTION 8", "Sec. 3 Term", "Art. 2", "Article 12.1", "article 12", "Clause 4",
    "§3 Fees", "§ 3", "Section hereof shall mean", "Articles 3", "Sections 2", "Secs. 4", "Art 5", "Clauses 1",
]


def test_the_vocabulary_is_declared_in_the_ttl_unchanged():
    v = load_segmentation_vocab()
    assert v["abbreviations"] == _ABBREV_BEFORE
    assert v["section_words"] == {"section", "article", "clause", "sec", "art"}
    assert v["section_symbols"] == {"§"}
    assert v["furniture_labels"] == {"by", "name", "title", "attest", "witness", "its", "date", "signature",
                                     "attention", "attn", "facsimile", "fax", "email", "e-mail", "telephone", "tel",
                                     "phone"}


def test_patterns_built_from_the_ttl_decide_as_the_literals_did():
    assert segment._ABBREV == _ABBREV_BEFORE
    for probe in _PROBES:
        assert bool(segment._FURNITURE_LINE.match(probe)) == bool(_FURNITURE_BEFORE.match(probe)), probe
        before, after = _SECTION_WORD_BEFORE.match(probe), segment._SECTION_WORD.match(probe)
        assert (before and before.group()) == (after and after.group()), probe
