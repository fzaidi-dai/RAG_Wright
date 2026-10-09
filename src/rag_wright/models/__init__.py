"""The model-profile seam (tech stack, assumption 2).

Model-neutral through OpenRouter by default (every role defaults to one product model; any role can be pointed
at another registered model through `EngineConfig.models` or the `RAG_MODEL_*` environment), with local open-model
deployment supported. Structured-output
calls go through this seam, keyed by model id, applied at the single point where a model is
constructed. Provider flags live in profile config and a dated ADR, never in node or agent code.
"""
