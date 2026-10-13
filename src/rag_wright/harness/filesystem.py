"""The matter's documents are not files (T-5a.11e, found by T-5a.11d's routing eval).

**The trace is the whole explanation:**

```
list_matter_documents()      -> {"documents":[{"ref":"acme-msa.pdf","document_id":"doc-1"}, ...]}
read_file("/acme-msa.pdf")   -> Error: File '/acme-msa.pdf' not found
glob("**/*")                 -> []
ls("/")                      -> []
read_file("/matter/acme-msa.pdf") -> not found
task(...)                    <- only now delegates
```

**A NAMESPACE COLLISION, not a description-similarity problem.** `create_deep_agent` always installs
`FilesystemMiddleware`, whose prompt tells the model *"You have access to a filesystem … All file paths
must start with a /"* — and our own listing hands back things called `acme-msa.pdf`. Opening them is the
obvious next move; `ls`'s description then says *"you should almost ALWAYS use this tool before
read_file"*, so it explores an empty filesystem and guesses more paths.

**Removing the filesystem tools was considered and rejected**, for two reasons that are worth keeping.
Memory recall is `glob` and `grep` over `/memories/`, not a single `read_file`, so removal would break
`AR-9`. And it would not have worked: the first wrong move was `read_file`, which would still happen with
fewer tools to flail with afterwards.

**Overriding the library's tool descriptions was also rejected**, though `HarnessProfileConfig` supports
it: pinning our behaviour to DeepAgents' internal wording re-breaks on every upgrade, silently.

**So the dead end becomes a signpost.** Today every wrong guess returns a bare `not found`, which is
exactly what invites the next guess — seven wasted calls in one run, and a run that burned to the tool-call
cap when it had no listing to anchor on. Guard by consequence: say what the thing actually is and which
tool reads it.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

#: The roots that really are the agent's own filesystem.
#:
#: `/memories/` is where `MemoryMiddleware` keeps notes — recall genuinely is `glob` and `grep` across it.
#: `/large_tool_results/` is DeepAgents' own overflow: it offloads an oversized tool result there and tells
#: the model to `read_file` it, so intercepting that would break the library's own mechanism.
#:
#: A guard asserts every path in `harness.memory.MEMORY_SOURCES` is covered here, because a second
#: hand-maintained copy would drift and the symptom would be memory recall redirected into a signpost.
MEMORY_ROOTS = ("/memories/", "/large_tool_results/")

#: The filesystem tools a wrong turn goes through, and where each keeps the path it was given.
_PATH_ARGUMENTS = {
    "read_file": ("file_path",),
    "write_file": ("file_path",),
    "edit_file": ("file_path",),
    "ls": ("path",),
    "glob": ("pattern", "path"),
    # **`grep`'s `pattern` is a REGEX, not a path**, and treating it as one broke the library's own
    # recovery route: `grep(path="/large_tool_results", pattern="x")` supplied two "paths", the regex was
    # not inside a memory root, and the signpost fired on a call DeepAgents tells the model to make.
    # Found by a test written for the cause-aware signpost (T-5a.12g), not by anything that ran before.
    "grep": ("path",),
}



#: The refusal when a product supplied no signpost text. **It names no tool**, because the substrate does
#: not know any -- and a redirect that invented one would send the model somewhere that does not exist.
_NO_SIGNPOST = (
    "That path is not on this agent's filesystem. This filesystem holds only your own notes under "
    "/memories/; the material you are working on is not files and cannot be opened with read_file, ls, "
    "glob or grep. Use the tools you were given."
)


def redirect_for(tool_name: str, args: dict[str, Any], *, signposts: Any = None) -> str | None:
    """The signpost for a filesystem call that has wandered out of the agent's own space, or `None`.

    **`None` is the common answer and the important one.** Memory recall and DeepAgents' result-offloading
    both go through these tools, so anything under `MEMORY_ROOTS` must reach the real tool untouched.
    """
    wanted = _PATH_ARGUMENTS.get(tool_name)
    if wanted is None:
        return None

    supplied = [str(args.get(name)) for name in wanted if args.get(name) not in (None, "")]
    if not supplied:
        # A `glob` with no path searches the backend's default root, which is the agent's own space.
        return None

    if all(_is_the_agents_own(value) for value in supplied):
        return None
    return _looking_for_subjects(supplied, signposts) or (
        signposts.not_a_filesystem if signposts is not None else _NO_SIGNPOST)





def _looking_for_subjects(supplied: list[str], signposts: Any = None) -> str | None:
    """A different answer for a search that is plainly hunting documents.

    **One signpost said the same sentence to every wrong turn.** It is true, and it answers none of them:
    a model that globbed `**/*.md` is looking for the documents it was just shown, and telling it where
    they are NOT leaves it to guess again. Eleven recorded filesystem calls split into this cause and a
    fall-back-after-an-empty-result; only this one can be recognised from the call itself.
    """
    if signposts is None:
        return None
    endings = tuple(signposts.subject_file_endings)
    return (signposts.looking_for_subjects
            if endings and any(value.lower().endswith(endings) for value in supplied) else None)


def _is_the_agents_own(value: str) -> bool:
    """Whether a path or pattern stays inside the agent's own space.

    **`/` is NOT the agent's own space**, and getting that wrong is how the first version passed the tests
    while leaving the real failure in place: `"/memories/".startswith("/")` is true, so a naive
    prefix-either-way check let `ls("/")` through -- the exact call the trace shows, returning `[]`, which
    reads as "the matter is empty".

    A parent of a memory root is allowed (`ls("/memories")`), because that is how recall starts.
    """
    inside = value.rstrip("/")
    if not inside:
        return False
    return any(value.startswith(root) or root.startswith(inside + "/") for root in MEMORY_ROOTS)


class TheSubjectIsNotAFilesystem(AgentMiddleware):
    """Turn a wrong filesystem call into a signpost instead of a silent `not found`.

    **It reads what the dispatch has already asked** (T-5a.12h), because the eleven filesystem calls we
    have recorded split into two causes and only one is visible in the call itself. A glob carrying a
    document extension is a model hunting the documents it was shown. The rest follow a product tool that
    came back empty -- `grep('Acme')` arrived straight after `trace_party_exposure` reported
    `resolved: False` -- and nothing in the `grep` says so. One signpost said the same sentence to both.
    """

    def __init__(self, seen: Any = None, *, signposts: Any = None) -> None:
        super().__init__()
        self._seen = seen
        #: The PRODUCT's signpost text (T-7.58). Ours named `ask_contract_question`, `sweep_matter` and
        #: `list_matter_documents`, so a second product's model was redirected to tools it does not have.
        #: `None` falls back to the generic refusal below, which names no tool at all.
        self._signposts = signposts

    def wrap_tool_call(self, request, handler):
        return self._redirect(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._redirect(request) or await handler(request)

    def _after_an_empty_answer(self, request) -> str | None:
        """What the dispatch's last empty result was, if this wrong turn follows one."""
        if self._seen is None:
            return None
        from rag_wright.harness.seen import run_key

        empty = self._seen.of(run_key(request)).last_answered_nothing()
        if empty is None:
            return None
        return (
            f"Your last search -- `{empty.tool}` -- came back empty, and this filesystem holds none of "
            "the material you are working on either, so searching it will also return nothing.\n\n"
            "**An empty result is an answer.** Report that nothing was found and say what you searched, "
            "or ask about it a different way with the tools you were given. Do not go looking somewhere "
            "that cannot hold it."
        )

    def _redirect(self, request) -> Any:
        """**A `ToolMessage`, never a raise.** An exception here would reach `ToolErrorMiddleware`, which
        would consult the taxonomy, find nothing, and halt the run — turning a recoverable wrong turn into
        a dead session."""
        from langchain_core.messages import ToolMessage

        call = request.tool_call
        signpost = redirect_for(call.get("name", ""), call.get("args") or {},
                                signposts=self._signposts)
        if signpost is None:
            return None
        # **The more specific answer wins.** A generic "these are not files" is true and answers nobody.
        signpost = self._after_an_empty_answer(request) or signpost
        return ToolMessage(content=signpost, tool_call_id=call.get("id", ""), name=call.get("name", ""))
