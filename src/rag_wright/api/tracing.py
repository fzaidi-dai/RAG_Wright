"""PS-3 (G18): trace correlation on the engine API surface.

The engine emits one Langfuse generation per model call when tracing is on (`RAG_TRACE_LEVEL` = `generations` or
`verbose`, AND `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` set); otherwise both helpers are no-ops. A product
groups the engine's generations with its own work:

    with traced_run(job_id=job.id, name="review-document", metadata={"tenant": tenant}):
        with traced_step("retrieve"):
            out = await ainvoke_subgraph(...)

`traced_run` puts every generation emitted inside the block under one trace/session (`job_id`, else
`document_id`) and flushes on exit; `traced_step` times a non-generation step as its own span. Tracing never breaks
the traced code: any Langfuse error degrades to a no-op.
"""
from __future__ import annotations

from rag_wright.models.tracing import (finish_generation, flush_tracing, start_generation, trace_level, traced_run,
                                       traced_step, tracing_on)

# PS-32: the switch, the level, open/close one generation around a call, and a flush, for a caller that emits its
# own generations (the agent harness, which builds its model with `agent_chat_model(..., trace=False)`).
__all__ = ["traced_run", "traced_step", "tracing_on", "trace_level", "start_generation", "finish_generation",
           "flush_tracing"]
