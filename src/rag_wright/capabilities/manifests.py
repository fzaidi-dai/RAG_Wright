"""Per-capability ARD manifest authoring (the committed source; T15 onward).

Every discoverable capability (FR-C / FR-I / FR-Q) authors one ARD manifest, written to the shared
registry root as `<slug>.json` under `urn:air:dreamai.io:rag_wright:<slug>`. The authoring data that
cannot be derived at registration — above all the 2-5 representative queries discovery ranks on — is
committed here, one `CapabilityManifest` per capability, added at that capability's own task. The
registry root itself is regenerable (outside the repo, `~/.air/registry`); this module is the durable
source, and `scripts/publish_manifests.py` writes every spec into the root.

Authoring reuses the T6 seam: the canonical-slug set and URN emitter (`registry.py`), the media-type
map and callable-bounds rule (`ard.py`), and `ManifestSkeleton.author(...)` for the validated
`RegistryEntry`. A live `RegistryStore` load is a GraphWright-side step, not done here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rag_wright.capabilities.ard import (
    CALLABLE_KINDS,
    MEDIA_TYPE_BY_KIND,
    CapabilityInterface,
    EntryKind,
    RegistryEntry,
    ResponseBounds,
    SkillRuntime,
    write_manifest,
)
from rag_wright.capabilities.registry import (
    ManifestSkeleton,
    canonical_capability_slugs,
    capability_urn,
)
from rag_wright.skills.rlm.agent import GRANTED_SUBAGENTS

# The RLM sub-agent roster the three RLM skills dispatch to, as the skill itself declares them
# (single source of truth in `skills/rlm/agent.py`). Since the recursive rebuild (T15/T17/T28) these are
# real Deep Agents sub-agents, so `grantedSubagents` is populated (ADR-0015; was `[]` pre-rebuild). Bound
# from `GRANTED_SUBAGENTS` so the manifest roster cannot drift from the names the skill actually declares
# and dispatches — a conformance test asserts the two are identical (drift passes here, fails GraphWright's
# bind).
_RLM_GRANTED = list(GRANTED_SUBAGENTS)


@dataclass(frozen=True)
class CapabilityManifest:
    """The committed ARD authoring data for one capability (what registration cannot derive)."""

    slug: str
    kind: EntryKind
    display_name: str
    description: str
    representative_queries: tuple[str, ...]  # 2-5; the field discovery ranks on
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()  # closure; agent_skill only
    skill_runtime: Optional[SkillRuntime] = None  # intrinsic runtime; agent_skill only
    golden_eval_ref: Optional[str] = None
    response_bounds: Optional[ResponseBounds] = None  # callable kinds only; defaults if omitted
    # GraphWright vendor extension (ADR-0030): the governed typed I/O. Declared only for the query-graph
    # capabilities GraphWright's checker verifies (the 5 + graph_query + generation); None elsewhere.
    capability_interface: Optional[CapabilityInterface] = None
    # EP-CORE-2 vendor extension (ADR-0118): the invoke factory for an INVOKABLE capability (subgraph/model), as a
    # "module:attr" import pointer to a `(resources, inputs) -> result` callable (model factories ignore resources).
    # ARD stays metadata-only (ADR-0003): this is a STRING pointer, not a callable. The invoker resolves + imports it
    # lazily, so there is no central engine-owned adapter dict — a developer registering a cap with an impl_ref makes
    # it invocable with zero engine edits. None for non-invokable kinds (function/agent_skill/mcp_tool) + reserved.
    impl_ref: Optional[str] = None


# One entry per capability, added at that capability's task. T15 registers the shared RLM method
# skill; the two RLM capabilities (rlm_chunking T17, rlm_synthesis T28) will `require` it.
_ENGINE_SPECS: tuple[CapabilityManifest, ...] = (
    CapabilityManifest(
        slug="rlm_method",
        kind="agent_skill",
        display_name="RLM divide-and-conquer method",
        description=(
            "The general recursive-language-model method (FR-C.10): load a working set into an "
            "interpreter, slice and dispatch the work in code, and synthesize the results, so the "
            "model never attends over the full volume. A shared skill required by the RLM chunking "
            "and RLM synthesis capabilities; it carries no determinism, boundary, or gating behavior "
            "of its own (those belong to the applying capability)."
        ),
        representative_queries=(
            "divide and conquer over a working set too large for a single prompt",
            "load a large working set into an interpreter and dispatch the work in code",
            "recursively call sub-models on small focused slices instead of the whole volume",
            "synthesize an answer from many partitioned sub-calls",
        ),
        tags=("rlm", "method", "divide-and-conquer"),
        # No capability_interface (T44): rlm_method is a shared METHOD skill `require`d by rlm_chunking and
        # rlm_synthesis (loaded knowledge), never bound as a data-processing node in a graph — it has no
        # pipeline data I/O of its own. The applying capability (chunking / synthesis) is what carries the
        # governed interface; governing the method here would be a type with no producer or consumer.
        # Intrinsic RLM runtime: the interpreter holds the working set and runs the code-side recursive
        # decompose(), dispatching the two real sub-agents (ADR-0015). granted_subagents is bound from the
        # skill's own roster so it cannot drift from what the skill declares/dispatches.
        skill_runtime=SkillRuntime(
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
    ),
    CapabilityManifest(
        slug="rlm_chunking",
        kind="agent_skill",  # applies the RLM method; loaded knowledge, requires rlm_method
        display_name="RLM chunking",
        description=(
            "Read a whole parsed document through an interpreter using the RLM method, split it along "
            "topic/section/chapter boundaries into semantically coherent chunks (capped ~20,000 "
            "tokens), and write a summary per chunk with a per-document manifest and stable "
            "chunk_ids. Deterministic and content-hash gated (FR-I.1)."
        ),
        representative_queries=(
            "chunk a long parsed document into semantically coherent sections",
            "split a document along topic and section boundaries within a token cap",
            "produce a summary per chunk and stable chunk ids",
            "re-chunk a document only when its content changes",
        ),
        requires=("rlm_method",),
        tags=("chunking", "rlm", "ingestion"),
        skill_runtime=SkillRuntime(  # LLM boundary discovery via the recursive machinery; real sub-agents
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
        capability_interface=CapabilityInterface(
            # Emits the ingestion `chunk` (id + text + summary + index) — NOT the query-side chunk_with_text;
            # embedding and graph_extraction consume this same `chunk`.
            inputs={"parsed": "parsed_doc"},
            outputs={"chunks": "chunk"},
            success_criterion="split a parsed document into semantically coherent, capped, summarized chunks with stable ids",
        ),
    ),
    # EP-CORE-1b-iii (ADR-0118): graph_extraction / entity_disambiguation / entity_resolution are INTERNAL steps of
    # `contract_ingestion_pipeline` (composed by direct import, no impl_ref, never invoked by ARD name), not
    # agent/product-facing standalone caps -- so they are NOT published to the reference pack. They remain canonical
    # FR-C slugs (`canonical_capability_slugs()`) and reserved-without-manifest, like `ontology_registry_derivation`.
    # DD-3/4/5 first removed their domain-vocab coupling (EntityId/EntityResolver seam; the entity/edge taxonomy).
    CapabilityManifest(
        slug="rlm_synthesis",
        kind="agent_skill",  # applies the RLM method; loaded knowledge, requires rlm_method
        display_name="RLM synthesis",
        description=(
            "Apply the RLM divide-and-conquer method to the retrieved candidate chunks: load them into "
            "an interpreter as data, slice in code (one focused unit per chunk), sub-call a model once "
            "per unit, and combine the outputs in a recursive code-side reduce — so synthesis never "
            "attends over the full chunk volume (FR-Q.5)."
        ),
        representative_queries=(
            "synthesize an answer from many retrieved chunks without attending over all at once",
            "divide-and-conquer synthesis over a large candidate set",
            "reduce retrieved passages into a focused synthesis for a query",
            "recursively combine per-chunk extracts into one answer",
        ),
        requires=("rlm_method",),
        tags=("rlm", "synthesis", "query"),
        skill_runtime=SkillRuntime(  # recursive descent (real sub-agents) + kept _reduce ascent
            needs_interpreter=True, rlm=True, requires_dynamic_dispatch=True, granted_subagents=_RLM_GRANTED
        ),
        capability_interface=CapabilityInterface(
            # Takes chunk_with_text (already rehydrated; does not fetch text). Emits the answer AND the
            # citations: cited_chunk_ids (the cited set) + cited_extracts (per-slice extract, each cited).
            inputs={"query": "text", "chunks": "chunk_with_text"},
            outputs={"answer": "text", "cited_chunk_ids": "chunk_id", "cited_extracts": "cited_extract"},
            success_criterion="recursively extract per-slice then reduce the chunks into a cited synthesis",
        ),
    ),
    CapabilityManifest(
        slug="generation",
        kind="agent_skill",  # a single grounded/cited LLM act; loaded, not called (CAP-REG-1)
        display_name="Answer generation (grounded, cited, abstains)",
        description=(
            "Produce a grounded, cited answer from the retrieved evidence — no claim without a citation, "
            "confidence-aware, abstaining when the context does not support an answer (FR-C.9, FR-Q.6). "
            "Split from vision-to-text so discovery ranks it only on answer-generation intents (ADR-0014)."
        ),
        representative_queries=(
            "answer a question grounded in the retrieved evidence with citations",
            "abstain when the retrieved context does not support an answer",
            "generate a confidence-aware cited answer from contract evidence",
            "produce a cited answer or an abstention from retrieved passages",
        ),
        tags=("generation", "answer", "grounded", "cited", "abstention"),
        capability_interface=CapabilityInterface(
            # Alt answer step to rlm_synthesis; also consumes chunk_with_text (evidence, already rehydrated).
            # Abstain is a boolean `abstained` flag on the answer record (empty citations), not a distinct
            # typed channel and nothing downstream gates on it — so it stays in the payload, noted here.
            inputs={"query": "text", "evidence": "chunk_with_text"},
            outputs={"answer": "text", "cited_chunk_ids": "chunk_id"},
            success_criterion="produce a grounded cited answer, or abstain (abstained flag, empty citations) when evidence does not support one",
        ),
    ),
    CapabilityManifest(
        slug="vision_to_text",
        kind="agent_skill",  # a single grounded vision-language act; SKILL.md, applied via the seam (SKILL-SPLIT)
        display_name="Vision-to-text (scanned-image transcription; authored skill)",
        description=(
            "Transcribe a scanned filing's images to text at ingestion on the Gemma 4 class model (FR-C.9), "
            "authored as skills/vision_to_text/SKILL.md: transcribe all visible text exactly, preserving "
            "reading order, output only the text. A single grounded vision-language act -- the ingestion-side "
            "twin of answer generation (also an agent_skill). Split from generation (ADR-0014): different "
            "inputs (an image, not evidence), different failure modes, a different caller (ingestion). "
            "Model-neutral through the seam (product = self-hosted Gemma-class, ADR-0039)."
        ),
        representative_queries=(
            "transcribe a scanned filing image to text",
            "extract the text from a scanned or image-only document",
            "convert a contract page image into machine-readable text at ingestion",
            "read text off a rasterized document image",
        ),
        tags=("vision-to-text", "ocr", "transcription", "ingestion"),
        capability_interface=CapabilityInterface(
            inputs={"image": "image"},
            outputs={"text": "text"},  # standalone ingestion transcription for image-only sources
            success_criterion="transcribe a scanned image to text at ingestion (image-only filings)",
        ),
    ),
    CapabilityManifest(
        slug="span_relevance_judgment",
        kind="agent_skill",  # a single grounded LLM relevance judgement; SKILL.md, applied via the seam (issue 0023)
        display_name="Span relevance judgment (span x condition -> verdict; authored skill)",
        description=(
            "Decide whether ONE retrieved span (a clause's operative text) actually addresses ONE structured "
            "condition being searched for (a clause type, optionally a value condition, with the question as "
            "context) -- returning a VERDICT (relevant | not_relevant | uncertain), not a similarity score, so no "
            "caller chooses a threshold (issue 0023, ADR-0088). The retrieval analog of the compliance judge and of "
            "answer abstention. Applied by typed_property_retrieval (Leg B) over the returned spans; the applying "
            "capability owns the verdict vocab + conservative default, this skill teaches only the reading."
        ),
        representative_queries=(
            "decide whether a retrieved clause actually addresses the searched condition",
            "judge a span as relevant, not_relevant, or uncertain for a clause-type + value condition",
            "return a relevance verdict for a retrieved span instead of a similarity score",
            "filter retrieved spans by whether they truly address the query condition",
        ),
        tags=("relevance", "judge", "retrieval", "verdict", "skill"),
    ),
    CapabilityManifest(
        slug="jev_decision",
        impl_ref="rag_wright.capabilities.jev_decision:jev_decision",
        kind="model",
        display_name="Jev typed-decision model (TypeSafe System-1, via OpenRouter)",
        description=(
            "A calibrated TYPED-DECISION model (ADR-0119): given a `state` and typed `questions` -- a yes/no "
            "(`noul`), a `choice` from a set, or a `score` -- it returns calibrated typed answers with no text in "
            "~70-500 ms. Used for the compliance closed-set decisions (operative-rule gate zero-shot ~0.92; "
            "claim_types / actor few-shot ~0.85/0.90) and for any domain's routing / tagging / screening. "
            "I/O-bound -> ASYNC: invoke via `ainvoke_model`. Laya is the open-weight / on-prem fallback."
        ),
        representative_queries=(
            "make a calibrated yes/no decision about a sentence",
            "classify text into a closed set of options with probabilities",
            "route or tag an input with a fast typed-decision model, no training",
        ),
        tags=("decision", "jev", "typesafe", "model"),
    ),
)

# EP-CORE-3 (ADR-0118): the engine ships an EMPTY ARD catalog. `MANIFEST_SPECS` is the runtime registry the
# DEVELOPER populates with their product's capabilities (via `register_capability`); the invoker + `capability_impl`
# read it live. The committed `_ENGINE_SPECS` above are the engine's GENERIC capabilities (`engine_capabilities()`);
# the REFERENCE PACK's manifests (the contract/compliance worked example, kept in the engine repo per ADR-0052) are
# `CONTRACT_SPECS` / `COMPLIANCE_SPECS` in `rag_wright.packs.{contracts,compliance}.pack`. Both are OPT-IN: nothing is registered until a pack is loaded
# (`load_pack(module)`, e.g. `load_reference_pack()`) or `register_capability` is called.
MANIFEST_SPECS: dict[str, CapabilityManifest] = {}


def register_capability(manifest: CapabilityManifest) -> None:
    """Register (or replace) one capability in the runtime ARD catalog. A product calls this for each of its
    domain capabilities (with an `impl_ref`); the invoker then resolves it by name with zero engine edits."""
    MANIFEST_SPECS[manifest.slug] = manifest


def engine_capabilities() -> tuple[CapabilityManifest, ...]:
    """ING-8b: the manifests of the engine's GENERIC capabilities (generation, the RLM skills, vision-to-text, the
    decision model, span relevance judgment). Opt-in, like every capability: the engine ships an empty catalog."""
    return _ENGINE_SPECS


# ING-8c: the reference pack is two domain packs; compliance builds on (and registers) contracts.
_REFERENCE_PACK_MODULES = ("rag_wright.packs.contracts.pack", "rag_wright.packs.compliance.pack")


def load_pack(module_name: str) -> None:
    """ING-8b: load a capability PACK by module name -- the module's `register()` adds its canonical slugs and
    registers its manifests (+ any engine capabilities it builds on). A product's own pack plugs in the same way."""
    import importlib

    importlib.import_module(module_name).register()


def reference_pack() -> tuple[CapabilityManifest, ...]:
    """The engine's committed REFERENCE PACK: the engine capabilities it uses + the contract/compliance worked
    example's manifests (`rag_wright.packs.contracts.pack` + `rag_wright.packs.compliance.pack`). Opt-in."""
    import importlib

    contracts, compliance = (importlib.import_module(m) for m in _REFERENCE_PACK_MODULES)
    return _ENGINE_SPECS + contracts.CONTRACT_SPECS + compliance.COMPLIANCE_SPECS


def load_reference_pack() -> None:
    """Register the engine's reference pack into the runtime catalog -- the opt-in worked example (the engine's own
    test suite loads it; a downstream product does NOT, registering its own capabilities instead)."""
    load_pack(_REFERENCE_PACK_MODULES[-1])  # the compliance pack registers contracts first


def author(slug: str) -> RegistryEntry:
    """Author a capability's committed spec into a complete, validated `RegistryEntry`."""
    if slug not in MANIFEST_SPECS:
        raise KeyError(f"no ARD manifest registered for {slug!r}; register one with register_capability() "
                       "or load the pack that provides it (load_pack)")
    spec = MANIFEST_SPECS[slug]
    if slug not in canonical_capability_slugs():
        raise ValueError(f"{slug!r} is not a canonical capability slug (SPEC.md section 5)")

    # callable kinds must declare response bounds; agent_skill is loaded, not called (carries none).
    bounds = (spec.response_bounds or ResponseBounds()) if spec.kind in CALLABLE_KINDS else None

    skeleton = ManifestSkeleton(
        name=spec.slug,
        kind=spec.kind,
        identifier=capability_urn(spec.slug),
        media_type=MEDIA_TYPE_BY_KIND[spec.kind],
        display_name=spec.display_name,
        response_bounds=bounds,
        description=spec.description,
        tags=list(spec.tags),
    )
    return skeleton.author(
        list(spec.representative_queries),
        requires=list(spec.requires) or None,
        skill_runtime=spec.skill_runtime,
        capability_interface=spec.capability_interface,
        golden_eval_ref=spec.golden_eval_ref,
    )


def publish(slug: str, *, root: Optional[Path] = None) -> Path:
    """Author `slug` and write its manifest to `<root>/<slug>.json` (default: the shared root)."""
    return write_manifest(author(slug), root=root)


def publish_all(*, root: Optional[Path] = None) -> list[Path]:
    """Author and write every specified manifest into the root. Returns the written paths."""
    return [publish(slug, root=root) for slug in MANIFEST_SPECS]
