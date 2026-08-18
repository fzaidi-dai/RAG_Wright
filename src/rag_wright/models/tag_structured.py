"""Client-side XML-tag structured output -- the app's LLM-AGNOSTIC structured-output mechanism (ADR-0045).

Server-side guided decoding (`response_format`/`with_structured_output` json_schema) is NOT portable: it runs
away to max length on self-hosted Gemma-4/vLLM and costs ~60s/call on OpenRouter->Cerebras, while PLAIN free-text
generation is fast and correct everywhere. So instead of forcing a JSON schema at decode time, we ask the model
to answer in light XML tags and parse them CLIENT-SIDE into the Pydantic contract. Tags (not JSON) because a
field body needs no escaping -- unlike a JSON string full of legal quotes/brackets/newlines.

`build_tag_structured` is a DROP-IN for `models.seam.build_structured` (same `(model_id, schema)` -> runnable
with `.invoke(prompt) -> schema instance`), so a caller swaps the mechanism by swapping the factory.

Scope now (query side, ADR-0045): FLAT schemas -- scalars (str/int/float/bool), enum/Literal, `str | None`, and
`list[<scalar>]`. That covers every query-side schema (generation, function classifier, query understanding,
highlight field-extract, reader judgments). `list[<BaseModel>]` (the one nested case, ingestion's
PropertyExtraction) is a documented EXTENSION POINT for a later task, not built here.
"""

from __future__ import annotations

import re
import types
import typing
from enum import Enum
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError

from rag_wright.models.seam import astream_text, build_model


def _unwrap_optional(ann: Any) -> Any:
    """`X | None` / `Optional[X]` -> `X`; otherwise unchanged."""
    if get_origin(ann) in (typing.Union, getattr(types, "UnionType", None)):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return ann


def _field_kind(ann: Any) -> tuple[bool, str]:
    """(is_list, human_hint) for a field annotation. Raises for `list[BaseModel]` (nested -- not yet supported)."""
    ann = _unwrap_optional(ann)
    if get_origin(ann) is list:
        item = (get_args(ann) or (str,))[0]
        if isinstance(item, type) and issubclass(item, BaseModel):
            raise NotImplementedError(
                "tag_structured: list[BaseModel] (nested) is a later extension point (ingestion PropertyExtraction)")
        return True, "one value per line"
    if isinstance(ann, type) and issubclass(ann, Enum):
        return False, "one of: " + " | ".join(str(e.value) for e in ann)
    if get_origin(ann) is typing.Literal:
        return False, "one of: " + " | ".join(str(v) for v in get_args(ann))
    if ann is bool:
        return False, "true or false"
    if ann in (int, float):
        return False, "a number"
    return False, "text"


def tag_instructions(schema: type[BaseModel]) -> str:
    """The prompt appendix telling the model to emit one XML-tag block per field of `schema`."""
    lines = ["Respond using EXACTLY these XML-style tags, one block per field. Put the RAW value between the "
             "tags -- no quotes, no JSON, no markdown fences:"]
    for name, field in schema.model_fields.items():
        is_list, hint = _field_kind(field.annotation)
        desc = (field.description or "").strip()
        suffix = f" -- {desc}" if desc else ""
        if is_list:
            lines.append(f"<{name}>\n({hint}){suffix}\n</{name}>")
        else:
            lines.append(f"<{name}>({hint}){suffix}</{name}>")
    lines.append("Omit the tag entirely for any field whose value is unknown or not applicable.")
    return "\n".join(lines)


def _extract(text: str, name: str) -> str | None:
    m = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None


def parse_tagged(text: str, schema: type[BaseModel]) -> BaseModel:
    """Parse XML-tagged free text into `schema`. Each `<field>` body is coerced by Pydantic (scalars, enums,
    bools, numbers); a `list[...]` field is split one-item-per-line (commas too). An absent tag is omitted so the
    field's default/optional applies. Pydantic does the final validation -- a missing required field raises
    ValidationError (which `build_tag_structured` retries)."""
    data: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        body = _extract(text, name)
        if body is None:
            continue
        is_list, _ = _field_kind(field.annotation)
        if is_list:
            data[name] = [x.strip() for x in re.split(r"[\r\n,]+", body.strip()) if x.strip()]
        else:
            data[name] = body.strip()
    return schema(**data)


class _TagStructuredRunnable:
    """A `build_structured`-shaped runnable that drives structured output client-side (free-text + tag parse)."""

    def __init__(
        self, model_id: str, schema: type[BaseModel], *, temperature: float, max_tokens: int | None, retries: int
    ) -> None:
        self._model_id = model_id
        self._schema = schema
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retries = retries

    def _full_prompt(self, prompt: Any) -> Any:
        # `prompt` is a plain string or a LangChain message sequence (e.g. [SystemMessage, HumanMessage]); append
        # the tag instructions as a trailing human turn either way.
        instr = tag_instructions(self._schema)
        return f"{prompt}\n\n{instr}" if isinstance(prompt, str) else [*prompt, ("human", instr)]

    def invoke(self, prompt: Any, config: Any = None) -> BaseModel:  # config accepted for runnable-compat, unused
        # Drop-in for build_structured.
        full = self._full_prompt(prompt)
        last: Exception | None = None
        for _ in range(self._retries + 1):
            text = str(build_model(
                self._model_id, temperature=self._temperature, max_tokens=self._max_tokens).invoke(full).content)
            try:
                return parse_tagged(text, self._schema)
            except ValidationError as exc:  # malformed/incomplete -> re-ask, bounded
                last = exc
        raise last  # type: ignore[misc]

    async def ainvoke(self, prompt: Any, config: Any = None) -> BaseModel:
        # ASYNC-A3 (ADR-0057): the async tag-parse structured path -- stream the free-text answer (idle-drip
        # detection + true wall-clock deadline via astream_text), then parse the light tags client-side, with the
        # same bounded re-ask on a ValidationError.
        full = self._full_prompt(prompt)
        last: Exception | None = None
        for _ in range(self._retries + 1):
            text = await astream_text(
                self._model_id, full, temperature=self._temperature, max_tokens=self._max_tokens)
            try:
                return parse_tagged(text, self._schema)
            except ValidationError as exc:
                last = exc
        raise last  # type: ignore[misc]


def build_tag_structured(
    model_id: str, schema: type[BaseModel], *, include_raw: bool = False, temperature: float = 0.0,
    max_tokens: int | None = 2048, retries: int = 1,
) -> _TagStructuredRunnable:
    """Drop-in for `models.seam.build_structured`: returns a runnable whose `.invoke(prompt)` yields a validated
    `schema` instance -- but via CLIENT-SIDE tag parsing (no server guided decoding), so it works on any model/
    provider. `max_tokens` defaults to a generous cap (free-text terminates on its own). `include_raw` is accepted
    for signature-compat but not supported (no query-side caller uses it)."""
    if include_raw:
        raise NotImplementedError("tag_structured: include_raw is not supported")
    return _TagStructuredRunnable(
        model_id, schema, temperature=temperature, max_tokens=max_tokens, retries=retries)
