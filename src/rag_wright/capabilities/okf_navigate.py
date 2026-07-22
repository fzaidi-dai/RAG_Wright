"""OKF navigation (FR-K.5/K.6, T50): the `okf_navigate` traversal capability.

Realizes the reachability ceiling (T47+T49 proved it is 0.845 for ACORD, model-free): a model navigates the
OKF bundle by progressive disclosure and returns a shortlist of concept ids, computing NO query-to-chunk
similarity. It is GENERIC over any OKF bundle -- it reasons over generic signposts (index entries, frontmatter,
descriptions, links), never over "clause" or "category" -- so the same capability navigates a legal-clause
bundle or a BigQuery-table bundle unchanged.

Structurally the RLM dynamic-sub-agent machinery (T15/T28) applied to a bundle instead of a flat working set:
one interpreter session (ADR-0020, KI-1) runs an authored navigation workflow that (a) uses PTC navigation
primitives -- deterministic bundle reads exposed as `tools.<name>()` -- to sift frontmatter and index entries
WITHOUT a model call, then (b) makes the two model decisions: a `okf_selector` sub-agent chooses which
signposts to expand, and the `judgeBodies` PTC tool judges concept bodies against the query -- a Python asyncio
fan-out (the embed_chunks pattern) through the profile seam, not a sub-agent, because the interpreter cannot
overlap `task()` dispatches (one JS engine per process). The query is threaded into the selector and the judge
by construction, not orchestrator choice (the T42 lesson). Bodies are read only after the frontmatter/index
sift, so bodies-read is a small fraction of candidates-considered.

The interpreter navigation is behind a `Navigator` seam: hermetic tests inject a stub navigator to exercise the
capability plumbing (trace, dedup, bounds, telemetry) without a model; the live `SeamNavigator` is opt-in.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import re
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from rag_wright.capabilities.registry import CapabilityRegistry
from rag_wright.models.profiles import ModelRole, model_for
from rag_wright.models.seam import build_model, build_structured
from rag_wright.okf.document import parse_okf
from rag_wright.skills.rlm.agent import _resolve_model, rlm_interpreter_session

OKF_SELECTOR = "okf_selector"
# The only sub-agent is the selector. Body relevance is judged by the `judgeBodies` PTC tool (Python asyncio
# fan-out), not a sub-agent: the interpreter cannot overlap `task()` dispatches (one JS engine per process,
# ADR-0020 / KI-1), so reader parallelism must live in Python, the embed_chunks pattern.
GRANTED_SUBAGENTS: tuple[str, ...] = (OKF_SELECTOR,)

_READER_CONCURRENCY = 8  # in-flight reader judgments per judgeBodies batch (bound against provider rate limits)

DEFAULT_MAX_DEPTH = 3
DEFAULT_FRONTIER_BUDGET = 50  # max concept bodies a traversal may read (the recall@50 evidence cap)


class Bounds(BaseModel):
    """Traversal run parameters, recorded with every result (RAC-50)."""

    max_depth: int = DEFAULT_MAX_DEPTH
    frontier_budget: int = DEFAULT_FRONTIER_BUDGET


class Telemetry(BaseModel):
    """Per-query cost telemetry. Serial round count is the primary latency proxy (fan-out within a round is
    parallel); bodies_read vs candidates_considered evidences the sift-before-read invariant."""

    serial_rounds: int = 0
    dispatches: int = 0
    candidates_considered: int = 0
    bodies_read: int = 0
    peak_frontier: int = 0
    max_depth_reached: int = 0
    wall_clock_s: float = 0.0


class TraceStep(BaseModel):
    """One navigation step, for the trace joined against gold in T51 (why a subtree was or was not expanded)."""

    depth: int
    action: str  # "read_index" | "select" | "read_body" | "expand_links" | "stop"
    path: str = ""
    detail: str = ""


class NavigationResult(BaseModel):
    """The `okf_navigate` output and capability contract: a concept-id shortlist plus the full trace."""

    query: str
    shortlist: list[str]  # concept ids (frontmatter chunk_id where present, else concept path)
    bounds: Bounds
    telemetry: Telemetry
    trace: list[TraceStep]


# --- generic OKF navigation primitives (FR-K.5): deterministic reads, no model call ---------------

_INDEX = "index.md"
_LINK = re.compile(r"\*\s*\[[^\]]*\]\(([^)]+)\)(?:\s*-\s*(.*))?")  # an index-entry line
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")  # any markdown link (for generic cross-link extraction)
_RELATED_HEADING = "## Related clauses"  # our compiler's cross-link section; read_body strips it as non-content


def _resolve_bundle_link(from_rel_path: str, link: str) -> Optional[str]:
    """Resolve a markdown link to a root-relative bundle concept path, or None if it is not one.

    Handles absolute-from-root (`/a/b.md`) and relative (`b.md`, `./b.md`, `../c/b.md`) links per OKF §5;
    drops external URLs, `mailto:`, anchor-only, and non-`.md` targets. Domain-agnostic — works on any bundle.
    """
    target = link.split("#", 1)[0].strip()
    if not target or "://" in target or target.startswith("mailto:") or not target.endswith(".md"):
        return None
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_rel_path), target))


class Signpost(BaseModel):
    """One index entry as a navigable signpost, with its path PRE-RESOLVED in Python.

    `path` is the root-relative target the workflow passes straight to the next `read_index` (a subdirectory)
    or `read_body` (a concept) -- so the emitted JS never does path arithmetic, which the model gets wrong.
    """

    path: str  # root-relative: the subdir to descend (is_dir) or the concept file to read
    description: str
    is_dir: bool


class OkfBundleReader:
    """Deterministic, model-free reads over any OKF bundle. The PTC surface the navigation workflow sifts with.

    Paths are bundle-root-relative (``""`` is the root). Nothing here assumes a domain: it reads index entries,
    frontmatter, bodies and links as the OKF spec defines them, so it works on any conformant bundle.
    """

    def __init__(self, bundle_root: Path) -> None:
        self._root = Path(bundle_root)

    def read_index(self, rel_dir: str = "") -> list[Signpost]:
        """The signposts listed in ``<rel_dir>/index.md`` (progressive disclosure); [] if none."""
        index = self._root / rel_dir / _INDEX
        if not index.exists():
            return []
        _, body = parse_okf(index.read_text(encoding="utf-8"))
        out: list[Signpost] = []
        for line in body.splitlines():
            m = _LINK.match(line.strip())
            if not m:
                continue
            link = m.group(1)
            is_dir = link.endswith("/") or link.endswith("/" + _INDEX)
            # resolve the (directory-relative or absolute) index link to a root-relative path, in Python
            resolved = link.lstrip("/") if link.startswith("/") else (f"{rel_dir}/{link}" if rel_dir else link)
            if is_dir:
                resolved = resolved[: -len("/" + _INDEX)] if resolved.endswith("/" + _INDEX) else resolved.rstrip("/")
            out.append(Signpost(path=resolved, description=(m.group(2) or "").strip(), is_dir=is_dir))
        return out

    def read_frontmatter(self, rel_path: str) -> dict:
        """The concept's frontmatter (the cheap sift performed before any body is read); {} if absent."""
        path = self._concept_path(rel_path)
        if path is None or not path.exists():
            return {}
        fm, _ = parse_okf(path.read_text(encoding="utf-8"))
        return fm

    def read_body(self, rel_path: str) -> str:
        """The concept's clause/body text (the expensive read the workflow threads to a reader sub-agent)."""
        path = self._concept_path(rel_path)
        if path is None or not path.exists():
            return ""
        _, body = parse_okf(path.read_text(encoding="utf-8"))
        idx = body.find(_RELATED_HEADING)
        return (body[:idx] if idx != -1 else body).strip()

    def related(self, rel_path: str) -> list[str]:
        """Outbound cross-links to other concepts: ANY bundle-internal markdown link in the body (OKF §5).

        Generic over any OKF bundle -- it does not depend on a ``## Related`` section (our compiler's
        convention); it resolves every markdown link (absolute-from-root or relative), keeps the ones that
        point at a concept file (`.md`) inside the bundle, deduplicates, and drops external/anchor/self links.
        Returns root-relative paths (the frontier the traversal expands along)."""
        path = self._concept_path(rel_path)
        if path is None or not path.exists():
            return []
        _, body = parse_okf(path.read_text(encoding="utf-8"))
        src = rel_path.lstrip("/")
        out: list[str] = []
        seen = {src}
        for m in _MD_LINK.finditer(body):
            target = _resolve_bundle_link(src, m.group(1))
            if target and target not in seen:
                seen.add(target)
                out.append(target)
        return out

    def concept_id(self, rel_path: str) -> str:
        """The concept's returned identifier: frontmatter ``chunk_id`` if present, else its bundle path."""
        fm = self.read_frontmatter(rel_path)
        return str(fm.get("chunk_id") or rel_path)

    def _concept_path(self, rel_path: str) -> Optional[Path]:
        target = rel_path.lstrip("/")
        if not target or not target.endswith(".md"):
            return None
        return self._root / target


# --- the Navigator seam ---------------------------------------------------------------------------


@runtime_checkable
class Navigator(Protocol):
    """Navigate the bundle for a query -> (concept-id shortlist, trace, telemetry). Stubbed in hermetic tests."""

    def navigate(self, query: str, reader: OkfBundleReader, bounds: Bounds) -> tuple[list[str], list[TraceStep], Telemetry]: ...


def okf_navigate(
    query: str,
    bundle_root: Path,
    *,
    navigator: Optional[Navigator] = None,
    bounds: Optional[Bounds] = None,
) -> NavigationResult:
    """Navigate `bundle_root` for `query`, returning a concept-id shortlist plus the trace and telemetry.

    `navigator` defaults to the live interpreter-driven `SeamNavigator`; a stub is injected for hermetic tests.
    No code path computes a query-to-chunk embedding similarity.
    """
    bounds = bounds or Bounds()
    reader = OkfBundleReader(bundle_root)
    navigator = navigator if navigator is not None else SeamNavigator()
    shortlist, trace, telemetry = navigator.navigate(query, reader, bounds)
    # dedup preserving order (RAC-50): a concept reached via several paths appears once
    seen: set[str] = set()
    deduped = [cid for cid in shortlist if not (cid in seen or seen.add(cid))]
    return NavigationResult(query=query, shortlist=deduped, bounds=bounds, telemetry=telemetry, trace=trace)


# --- the live interpreter-driven navigator (the model WRITES the workflow, taught by the Skill) --------

_SKILL_PATH = Path(__file__).parents[1] / "skills" / "okf_navigate" / "SKILL.md"

_SELECTOR_PROMPT = (
    "You navigate a knowledge bundle to answer a QUESTION. You are given a numbered list of signposts "
    "(each a name, a one-line description, and whether it is a subdirectory). Choose which are worth "
    "exploring to answer the question; prefer precision, do not select everything. You never see the "
    "underlying documents, only the signposts. Reply with ONLY a JSON object of the integer indices to "
    'explore, e.g. {"keep": [0, 3, 4]}, and nothing else.'
)
_READER_JUDGE_PROMPT = (
    "You judge one document against a QUESTION: is it relevant evidence for answering it? You see one "
    "document at a time, never the whole bundle. Decide relevant / not relevant."
)


class _Relevance(BaseModel):
    """The reader judgment schema, forced through the profile seam (build_structured) -- the profile applies
    DeepSeek's structured method and disables thinking on this forced call, so it never hits the reasoning-mode
    tool_choice rejection that `task(responseSchema)` did."""

    relevant: bool


def navigate_method() -> str:
    """The OKF-navigation method (the Skill body, YAML frontmatter stripped) as the orchestrator's system
    prompt. It teaches the progressive-disclosure method and the canonical workflow the model writes into
    `eval`; the model authors the program, it is not hardcoded here (the dynamic-subagents paradigm)."""
    text = _SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        marker = text.find("\n---", 3)
        if marker != -1:
            text = text[marker + 4 :]
    return text.strip()


def _with_question(prompt: str, query: str) -> str:
    """Bake the question into a sub-agent's system prompt, so query-relevance is enforced, not left to the
    orchestrator to thread into each dispatch (the FR-Q.5 / T42 lesson)."""
    return f"{prompt}\n\nThe QUESTION to answer (judge relevance to THIS, verbatim):\n{query}"


def _selector_config(query: str, model) -> SubAgent:
    return {"name": OKF_SELECTOR, "description": "Chooses which OKF signposts to explore for a question.",
            "system_prompt": _with_question(_SELECTOR_PROMPT, query),
            "model": _resolve_model(model, ModelRole.STRUCTURED_REASONING)}


def _navigation_ptc(reader: OkfBundleReader, bounds: Bounds, query: str, reader_model_id: str):
    """The PTC navigation primitives. The interpreter renders their signatures, so the model writes correct
    object-argument calls (e.g. tools.readIndex({rel_dir})). Selection lives in the selector sub-agent, but
    body relevance is judged HERE, by `judge_bodies`, so it can fan out in Python (the interpreter cannot
    overlap sub-agent dispatches). The query and reader model are bound in by construction, not passed by JS."""

    @tool
    def frontier_budget() -> int:
        """The maximum number of concept bodies the traversal may read."""
        return bounds.frontier_budget

    @tool
    def read_index(rel_dir: str = "") -> list:
        """List the index signposts under a bundle directory (each {path, description, is_dir}); no body read."""
        return [s.model_dump() for s in reader.read_index(rel_dir)]

    @tool
    def read_body(rel_path: str) -> str:
        """Read one concept's body text (the expensive read, performed only after the signpost sift)."""
        return reader.read_body(rel_path)

    @tool
    def related(rel_path: str) -> list:
        """Outbound related links from a concept (lateral frontier expansion)."""
        return reader.related(rel_path)

    @tool
    def concept_id(rel_path: str) -> str:
        """The concept's returned identifier (frontmatter chunk_id where present)."""
        return reader.concept_id(rel_path)

    @tool
    async def judge_bodies(rel_paths: list[str]) -> list[bool]:
        """Read and judge many concept bodies against the question CONCURRENTLY, returning a parallel list of
        booleans (one per input path, order preserved). This is where reader parallelism lives: the fan-out is
        Python asyncio (Semaphore + gather + to_thread, the embed_chunks pattern), so it is NOT bottlenecked by
        the single-JS-engine limit that serializes sub-agent dispatches. Each judgment goes through the profile
        seam (build_structured), never a hardcoded provider flag."""
        judge = build_structured(reader_model_id, _Relevance)
        system = _with_question(_READER_JUDGE_PROMPT, query)
        semaphore = asyncio.Semaphore(_READER_CONCURRENCY)

        async def _one(rel_path: str) -> bool:
            body = reader.read_body(rel_path)
            if not body:
                return False
            async with semaphore:
                verdict = await asyncio.to_thread(
                    judge.invoke,
                    [SystemMessage(content=system), HumanMessage(content=f"Document:\n{body}")],
                )
            return bool(getattr(verdict, "relevant", False))

        return list(await asyncio.gather(*(_one(p) for p in rel_paths)))

    return [frontier_budget, read_index, read_body, related, concept_id, judge_bodies]


_NAVIGATE_REQUEST = (
    "Run this as a workflow: emit the OKF navigation workflow from your instructions to the `eval` tool now, "
    "in one call. It reads the bundle via tools.readIndex / tools.readBody, dispatches the okf_selector "
    "sub-agent, and judges bodies with tools.judgeBodies. The bundle is NOT on any filesystem, so do NOT use "
    "ls, glob, or read_file. Do NOT "
    'answer from your own knowledge. Return ONLY the eval result (`{"shortlist": [...]}`).'
)


def _extract_shortlist(messages) -> list[str]:
    """The result is an eval tool return (deterministic), not the chat message (GraphWright interpreter_output).

    The model may run a *diagnostic* eval after the workflow (e.g. re-reading an index), so scan eval results
    newest-first and take the first one that carries a `shortlist` key -- not merely the last eval.
    """
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "eval":
            content = str(message.content)
            if '"shortlist"' in content:
                return _parse_shortlist(content)
    return []


class SeamNavigator:
    """The live navigator: one interpreter session over which the model, taught by the okf_navigate Skill,
    WRITES the navigation workflow (it is not hardcoded), dispatches the okf_selector sub-agent, and judges
    bodies via the judgeBodies PTC tool. Per-role models resolve through the profile seam (STRUCTURED_REASONING
    / DeepSeek V4 Pro) or are injected (tests). Gemma is enrichment-only (ADR-0023); the navigation judgments
    run on the strong model."""

    def __init__(
        self, model: object = None, *, selector_model: object = None, reader_model: object = None,
        config: Optional[dict] = None,
    ) -> None:
        self._model = model
        self._selector_model = selector_model
        self._reader_model = reader_model
        self._config = config or {}  # langchain invoke config (Langfuse callbacks, recursion_limit backstop)

    def navigate(self, query: str, reader: OkfBundleReader, bounds: Bounds):
        # Attach any Langfuse callback to the MODELS (not just the invoke config): the interpreter runs eval
        # on a worker thread (run_coroutine_threadsafe), which drops the callback/OTEL context, so sub-agent
        # calls are only traced if the callback is bound at model construction.
        callbacks = self._config.get("callbacks") or None

        def resolve(arg: object) -> BaseChatModel:
            if isinstance(arg, BaseChatModel):
                return arg
            return build_model(arg or model_for(ModelRole.STRUCTURED_REASONING), callbacks=callbacks)

        orchestrator = resolve(self._model)
        selector = resolve(self._selector_model if self._selector_model is not None else self._model)
        # The reader is a PTC tool (build_structured), which needs a model-id string, not a built model. Prefer
        # an explicit id; fall back to the STRUCTURED_REASONING default. (A BaseChatModel injected as the reader
        # cannot drive the seam's structured path, so only the live id/None path is supported for judging.)
        reader_model_id = next(
            (m for m in (self._reader_model, self._model) if isinstance(m, str)),
            model_for(ModelRole.STRUCTURED_REASONING),
        )
        start = time.perf_counter()
        # raise the eval-result cap: the shortlist + decision log at a high frontier budget exceeds the
        # interpreter's 4,000-char default, which would truncate the JSON mid-string and lose the shortlist.
        ptc = _navigation_ptc(reader, bounds, query, reader_model_id)
        with rlm_interpreter_session(ptc=ptc, max_result_chars=200_000) as interpreter:
            agent = create_deep_agent(
                model=orchestrator,
                tools=[],
                system_prompt=navigate_method(),  # the Skill method (the model writes the workflow from it)
                subagents=[_selector_config(query, selector)],
                middleware=[interpreter],
            )
            messages = agent.invoke({"messages": [HumanMessage(content=_NAVIGATE_REQUEST)]}, config=self._config)["messages"]
        elapsed = time.perf_counter() - start
        shortlist = _extract_shortlist(messages)
        return shortlist, [], Telemetry(wall_clock_s=elapsed, bodies_read=len(shortlist))


def _parse_shortlist(text: str) -> list[str]:
    """Extract the shortlist from the workflow's JSON result. Never raises (returns [])."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "shortlist" in value and isinstance(value["shortlist"], list):
            return [str(c) for c in value["shortlist"]]
    # truncation-robust fallback: pull the `"shortlist": [ ... ]` array even if the outer object was cut off
    m = re.search(r'"shortlist"\s*:\s*\[(.*?)(?:\]|$)', text, re.DOTALL)
    if m:
        ids = re.findall(r'"([^"]+)"', m.group(1))
        if ids:
            return ids
    return []


def register_okf_navigate(registry: CapabilityRegistry) -> None:
    """Register `okf_navigate` (FR-K.6): a query-discovered `agent_skill` (category 1; an ARD manifest follows)."""
    registry.register(
        "okf_navigate",
        contract=NavigationResult,
        kind="agent_skill",
        display_name="OKF navigation (embedding-free progressive-disclosure traversal)",
    )
