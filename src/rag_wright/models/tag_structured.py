"""Client-side XML-tag structured output -- the app's LLM-AGNOSTIC structured-output mechanism (ADR-0045).

Server-side guided decoding (`response_format`/`with_structured_output` json_schema) is NOT portable: it runs
away to max length on self-hosted Gemma-4/vLLM and costs ~60s/call on OpenRouter->Cerebras, while PLAIN free-text
generation is fast and correct everywhere. So instead of forcing a JSON schema at decode time, we ask the model
to answer in light XML tags and parse them CLIENT-SIDE into the Pydantic contract. Tags (not JSON) because a
field body needs no escaping -- unlike a JSON string full of legal quotes/brackets/newlines.

`build_tag_structured` is a DROP-IN for `models.seam.build_structured` (same `(model_id, schema)` -> runnable
with `.invoke(prompt) -> schema instance`), so a caller swaps the mechanism by swapping the factory.

Scope (ADR-0045 + TAGPARSE-INGEST-1a): FLAT schemas -- scalars (str/int/float/bool), enum/Literal, `str | None`,
`list[<scalar>]` -- AND NESTED schemas: a single nested `BaseModel` field and `list[<BaseModel>]`, emitted and
parsed by recursion (nested `<field><sub>..</sub></field>`; list items as repeated `<item>..</item>` blocks).
That covers the query-side schemas (generation, query understanding, highlight field-extract, reader judgments),
the flat ingest judges (extraction_semantic_judge -> SemanticVerdict), AND the ingestion extraction contracts
(Clause with its nested bounded_by/caps/governed_by + the `excepts` list; ContractParties with `parties`), which
TAGPARSE-INGEST-1 routes off docling-graph's server-side json_object onto this path. The ingest clause-function
classifier (nested `list[SpanFunctions]` / `list[RawScore]`) still uses its OWN bespoke free-text tags + client-
side parse in `spans/clause_function_classifier.py`, not this generic parser (issue 0005), and is unchanged.
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


def _classify(ann: Any) -> tuple[str, type[BaseModel] | None, str]:
    """Classify a field annotation into `(kind, submodel, hint)`:
      - 'scalar'      : a single scalar/enum/Literal/bool/number value  (submodel None)
      - 'list_scalar' : `list[<scalar/enum>]`                           (submodel None)
      - 'nested'      : a single nested `BaseModel`                     (submodel = that model)
      - 'nested_list' : `list[<BaseModel>]`                            (submodel = the item model)
    Nested kinds (TAGPARSE-INGEST-1a) let the ingestion contracts (Clause's bounded_by/caps/governed_by + the
    `excepts` list, ContractParties' `parties` list) round-trip through tags; the emitter/parser recurse."""
    ann = _unwrap_optional(ann)
    if get_origin(ann) is list:
        item = (get_args(ann) or (str,))[0]
        if isinstance(item, type) and issubclass(item, BaseModel):
            return "nested_list", item, ""
        return "list_scalar", None, "one value per line"
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return "nested", ann, ""
    if isinstance(ann, type) and issubclass(ann, Enum):
        return "scalar", None, "one of: " + " | ".join(str(e.value) for e in ann)
    if get_origin(ann) is typing.Literal:
        return "scalar", None, "one of: " + " | ".join(str(v) for v in get_args(ann))
    if ann is bool:
        return "scalar", None, "true or false"
    if ann in (int, float):
        return "scalar", None, "a number"
    return "scalar", None, "text"


def _field_lines(schema: type[BaseModel], fields: set[str] | None = None) -> list[str]:
    """One tag-template block per field of `schema`; recurses into nested models and list[model] items. `fields`
    (if given) restricts to that subset -- for the per-group ingestion passes (TAGPARSE-INGEST-1b)."""
    out: list[str] = []
    for name, field in schema.model_fields.items():
        if fields is not None and name not in fields:
            continue
        kind, sub, hint = _classify(field.annotation)
        desc = (field.description or "").strip()
        # Guidance goes AFTER the tags (a trailing `-- ...`), NOT inside them: a hint placed inside the tag body
        # (e.g. `(one of: a | b)`) gets ECHOED by the model (`<f>(a)</f>`), which then fails value/enum parsing.
        # The tag body is left EMPTY for the model to fill with ONLY the value.
        guide = "; ".join(g for g in (hint, desc) if g)
        tail = f"  -- {guide}" if guide else ""
        if kind == "scalar":
            out.append(f"<{name}></{name}>{tail}")
        elif kind == "list_scalar":
            out.append(f"<{name}>\n</{name}>{tail}")
        elif kind == "nested":
            inner = "\n".join(_field_lines(sub))  # type: ignore[arg-type]
            out.append(f"<{name}>{(' -- ' + desc) if desc else ''}\n{inner}\n</{name}>")
        else:  # nested_list
            inner = "\n".join(_field_lines(sub))  # type: ignore[arg-type]
            out.append(f"<{name}>{(' -- ' + desc) if desc else ''} (repeat the <item> block once per entry)\n"
                       f"<item>\n{inner}\n</item>\n</{name}>")
    return out


def tag_instructions(schema: type[BaseModel], fields: set[str] | None = None) -> str:
    """The prompt appendix telling the model to emit one XML-tag block per field of `schema` (recursing into
    nested models and list[model] items). `fields` (if given) restricts to that subset."""
    return "\n".join([
        "Respond using EXACTLY these XML-style tags, one block per field. Fill EACH tag body with ONLY the raw "
        "value -- no quotes, no JSON, no markdown fences, and do NOT copy the guidance. Any text after `--` is "
        "guidance for you, not part of the value:",
        *_field_lines(schema, fields),
        "Omit the tag entirely for any field whose value is unknown or not applicable.",
    ])


def _extract(text: str, name: str) -> str | None:
    m = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None


def _field_value(body: str, field: Any, lenient: bool, full_text: str | None = None) -> Any:
    """The Python value for one `<field>` body: scalar/enum/bool/number verbatim; `list[<scalar>]` split
    one-per-line (commas too); a nested `BaseModel` recursed; a `list[<BaseModel>]` split on its `<item>` blocks
    and each recursed. In `lenient` mode an unbuildable list ITEM is dropped (kept in strict).

    For a nested `BaseModel`, the sub-model is parsed from `full_text` when given, not just the `<field>` body:
    models often FLATTEN a nested field -- emitting `<governed_by>Delaware</governed_by>` then the sub-fields
    `<jurisdiction_name>...`/`<law_multiplicity>...` as SIBLINGS rather than nested inside. Sub-field tag names are
    unique in these contracts, so scanning the full text finds them whether nested or flattened."""
    kind, sub, _ = _classify(field.annotation)
    if kind == "scalar":
        return body.strip()
    if kind == "list_scalar":
        return [x.strip() for x in re.split(r"[\r\n,]+", body.strip()) if x.strip()]
    if kind == "nested":
        return parse_tagged(full_text if full_text is not None else body, sub, lenient=lenient)  # type: ignore[arg-type]
    # nested_list
    out = []
    for it in re.findall(r"<item>(.*?)</item>", body, re.DOTALL | re.IGNORECASE):
        try:
            out.append(parse_tagged(it, sub, lenient=lenient))  # type: ignore[arg-type]
        except ValidationError:
            if not lenient:
                raise
    return out


def _prune_invalid_optionals(schema: type[BaseModel], data: dict[str, Any], err: ValidationError) -> BaseModel:
    """Drop each NON-required top field the ValidationError blames (a partial nested block, a constraint-violating
    scalar) so it falls back to its default, then rebuild ONCE. A required field cannot be omitted, so if the
    error is (also) on a required field the rebuild re-raises -- the caller's graceful-degrade contract then owns
    it. Only used on the last, lenient attempt."""
    pruned = False
    for e in err.errors():
        loc = e.get("loc") or ()
        if not loc:
            continue
        top = loc[0]
        f = schema.model_fields.get(top)  # type: ignore[arg-type]
        if f is not None and not f.is_required() and top in data:
            del data[top]
            pruned = True
    if not pruned:
        raise err  # nothing omittable (the failure is on required data) -> genuine, propagate
    return schema(**data)


def parse_tagged(text: str, schema: type[BaseModel], *, lenient: bool = False,
                 fields: set[str] | None = None) -> BaseModel:
    """Parse XML-tagged free text into `schema`. An absent tag is omitted so the field's default/optional applies;
    Pydantic does the final validation. STRICT (default): a missing required field or a malformed/partial nested
    block raises ValidationError (which `build_tag_structured` retries -- the re-ask). LENIENT (the last attempt,
    TAGPARSE-INGEST-1a): omit-to-default -- a NON-required field whose value fails to validate (an unbuildable
    nested block, a constraint-violating scalar, an invalid list item) is dropped to its default rather than
    failing the whole extraction; a missing REQUIRED field still raises."""
    data: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        if fields is not None and name not in fields:
            continue
        body = _extract(text, name)
        if body is None:
            continue
        try:
            data[name] = _field_value(body, field, lenient, full_text=text)
        except ValidationError:
            if not lenient:  # strict: let the re-ask handle it; lenient: omit this field (default applies)
                raise
    try:
        return schema(**data)
    except ValidationError as exc:
        if not lenient:
            raise
        return _prune_invalid_optionals(schema, data, exc)


class _TagStructuredRunnable:
    """A `build_structured`-shaped runnable that drives structured output client-side (free-text + tag parse)."""

    def __init__(
        self, model_id: str, schema: type[BaseModel], *, temperature: float, max_tokens: int | None, retries: int,
        label: str | None = None, fields: set[str] | None = None,
    ) -> None:
        self._model_id = model_id
        self._schema = schema
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retries = retries
        self._label = label  # ADR-0058: stage/call-site name for the deadline warning (threaded to astream_text)
        self._fields = fields  # TAGPARSE-INGEST-1b: restrict this pass to a field subset (per-group extraction)

    def _full_prompt(self, prompt: Any) -> Any:
        # `prompt` is a plain string or a LangChain message sequence (e.g. [SystemMessage, HumanMessage]); append
        # the tag instructions as a trailing human turn either way.
        instr = tag_instructions(self._schema, fields=self._fields)
        return f"{prompt}\n\n{instr}" if isinstance(prompt, str) else [*prompt, ("human", instr)]

    def invoke(self, prompt: Any, config: Any = None) -> BaseModel:  # config accepted for runnable-compat, unused
        # Drop-in for build_structured. The re-ask is STRICT (so a malformed/partial answer is re-asked); the
        # FINAL attempt is LENIENT (omit-to-default) so a persistently-partial nested block degrades rather than
        # failing the whole extraction (TAGPARSE-INGEST-1a).
        full = self._full_prompt(prompt)
        attempts = self._retries + 1
        last: Exception | None = None
        for i in range(attempts):
            text = str(build_model(
                self._model_id, temperature=self._temperature, max_tokens=self._max_tokens).invoke(full).content)
            try:
                return parse_tagged(text, self._schema, lenient=(i == attempts - 1), fields=self._fields)
            except ValidationError as exc:  # malformed/incomplete -> re-ask, bounded
                last = exc
        raise last  # type: ignore[misc]

    async def ainvoke(self, prompt: Any, config: Any = None) -> BaseModel:
        # ASYNC-A3 (ADR-0057): the async tag-parse structured path -- stream the free-text answer (idle-drip
        # detection + true wall-clock deadline via astream_text), then parse the light tags client-side, with the
        # same bounded re-ask on a ValidationError.
        full = self._full_prompt(prompt)
        attempts = self._retries + 1
        last: Exception | None = None
        for i in range(attempts):
            text = await astream_text(
                self._model_id, full, temperature=self._temperature, max_tokens=self._max_tokens,
                label=self._label)
            try:
                return parse_tagged(text, self._schema, lenient=(i == attempts - 1), fields=self._fields)
            except ValidationError as exc:
                last = exc
        raise last  # type: ignore[misc]


def build_tag_structured(
    model_id: str, schema: type[BaseModel], *, include_raw: bool = False, temperature: float = 0.0,
    max_tokens: int | None = 2048, retries: int = 1, label: str | None = None, fields: set[str] | None = None,
) -> _TagStructuredRunnable:
    """Drop-in for `models.seam.build_structured`: returns a runnable whose `.invoke(prompt)` yields a validated
    `schema` instance -- but via CLIENT-SIDE tag parsing (no server guided decoding), so it works on any model/
    provider. `max_tokens` defaults to a generous cap (free-text terminates on its own). `label` (ADR-0058) names
    the stage/call-site in the deadline warning. `fields` (TAGPARSE-INGEST-1b) restricts emission+parsing to a
    subset of `schema`'s fields -- for per-group ingestion passes over one big schema. `include_raw` is accepted
    for signature-compat but not supported (no query-side caller uses it)."""
    if include_raw:
        raise NotImplementedError("tag_structured: include_raw is not supported")
    return _TagStructuredRunnable(
        model_id, schema, temperature=temperature, max_tokens=max_tokens, retries=retries, label=label,
        fields=fields)
