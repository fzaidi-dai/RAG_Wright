"""OKF concept-document (de)serialization: YAML frontmatter + markdown body.

Mirrors the OKF reference `OKFDocument` (knowledge-catalog/okf) on our stack: a document is a `---`
delimited YAML frontmatter block followed by a markdown body. `serialize_okf` preserves key order and
appends the body verbatim (so a chunk body stays byte-faithful to its sidecar text); `parse_okf`
round-trips it. YAML handles the escaping of descriptions that carry colons, quotes, or brackets, so
the messy-real-text failure mode does not reach the frontmatter.
"""

from __future__ import annotations

from typing import Any

import yaml

_DELIM = "---"


class OkfParseError(ValueError):
    """A concept document whose frontmatter is unterminated or is not a YAML mapping."""


def serialize_okf(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a concept document: `---` frontmatter (key order preserved) then the body verbatim."""
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
    body = body if body.endswith("\n") else body + "\n"
    return f"{_DELIM}\n{fm_text}\n{_DELIM}\n\n{body}"


def parse_okf(text: str) -> tuple[dict[str, Any], str]:
    """Parse a concept document into (frontmatter, body). No frontmatter -> ({}, whole text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _DELIM), None)
    if end is None:
        raise OkfParseError("unterminated YAML frontmatter block")
    try:
        fm = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as e:
        raise OkfParseError(f"invalid YAML in frontmatter: {e}") from e
    if not isinstance(fm, dict):
        raise OkfParseError("frontmatter must be a YAML mapping")
    body = "\n".join(lines[end + 1 :])
    if body.startswith("\n"):
        body = body[1:]
    return fm, body
