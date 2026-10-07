"""ADR-0066 P1b-1: introspect the hand-maintained clause extraction template (`clause_template.py`) into a
structured field spec -- the SHARED source used by both the bootstrap emitter (writes the specs into the ttl) and
the drift test (asserts the ttl captured them faithfully). One introspection, so emitter and test cannot diverge.

Keyed by TEMPLATE FIELD (`<Model>.<field>`), because the template is not a flat dimension->field map: it has
nested constraint models (CapConstraint/TemporalConstraint/Jurisdiction) and non-dimension fields (clause_type,
document_reference), and field names do not uniformly match dimension values (excepts<->carve_out).
"""

from __future__ import annotations

import enum
import typing
from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from rag_wright.packs.contracts.ontology import clause_template as ct

# The models whose fields make up the extraction template (root + nested constraint models).
_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("Clause", ct.Clause),
    ("CapConstraint", ct.CapConstraint),
    ("TemporalConstraint", ct.TemporalConstraint),
    ("Jurisdiction", ct.Jurisdiction),
)


@dataclass(frozen=True)
class TemplateFieldSpec:
    """One field of the extraction template -- everything needed to regenerate it (P1b-2) + its knowledge."""

    model: str
    name: str
    kind: str                       # scalar_enum | list_enum | list_str | optional_str | str | model_ref
    default_token: str              # required | none | list | enum:<value> | model_none
    definition: str = ""            # the field's LOOK-FOR description (verbatim; "" for the TODO gaps)
    enum_class: str | None = None   # the Enum class name (scalar_enum / list_enum)
    model_ref: str | None = None    # the nested model name (model_ref)
    edge_label: str | None = None   # the docling-graph edge label (nested refs)
    max_length: int | None = None
    examples: tuple[str, ...] = field(default_factory=tuple)


def _enum_class(ann: typing.Any) -> type[enum.Enum] | None:
    for a in [ann, *typing.get_args(ann)]:
        for b in [a, *typing.get_args(a)]:
            if isinstance(b, type) and issubclass(b, enum.Enum):
                return b
    return None


def _model_class(ann: typing.Any) -> type[BaseModel] | None:
    for a in [ann, *typing.get_args(ann)]:
        if isinstance(a, type) and issubclass(a, BaseModel):
            return a
    return None


def _spec(model_name: str, name: str, f: typing.Any) -> TemplateFieldSpec:
    ann = f.annotation
    enum_cls = _enum_class(ann)
    model_cls = _model_class(ann)
    is_list = typing.get_origin(ann) is list
    js = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
    max_length = next((getattr(m, "max_length", None) for m in (f.metadata or [])
                       if getattr(m, "max_length", None) is not None), None)

    if model_cls is not None:
        kind, default_token = "model_ref", "model_none"
    elif is_list and enum_cls is not None:
        kind, default_token = "list_enum", "list"
    elif is_list:  # issue 0037: List[str] -- an OPEN descriptive list dim (verbatim capture, canonicalized at KG)
        kind, default_token = "list_str", "list"
    elif enum_cls is not None:
        kind = "scalar_enum"
        default_token = f"enum:{f.default.value}" if isinstance(f.default, enum.Enum) else "required"
    else:  # str / Optional[str]
        kind = "optional_str" if typing.get_origin(ann) is typing.Union else "str"
        default_token = "required" if f.default is PydanticUndefined else "none"

    return TemplateFieldSpec(
        model=model_name, name=name, kind=kind, default_token=default_token,
        definition=f.description or "",
        enum_class=enum_cls.__name__ if enum_cls is not None else None,
        model_ref=model_cls.__name__ if model_cls is not None else None,
        edge_label=js.get("edge_label"),
        max_length=max_length,
        examples=tuple(f.examples or ()),
    )


def introspect_template_fields() -> list[TemplateFieldSpec]:
    """The template's fields as structured specs (stable order: model order, then field-declaration order)."""
    specs: list[TemplateFieldSpec] = []
    for model_name, model in _MODELS:
        for name, f in model.model_fields.items():
            specs.append(_spec(model_name, name, f))
    return specs
