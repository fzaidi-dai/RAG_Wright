"""The model-profile seam (tech stack, assumption 2).

Model-neutral through OpenRouter by default (Gemma 4 class for reasoning, generation,
vision-to-text, and RLM; a smaller model for chunking and summarization; a larger model for
quality-sensitive extraction), with local open-model deployment supported. Structured-output
calls go through this seam, keyed by model id, applied at the single point where a model is
constructed. Provider flags live in profile config and a dated ADR, never in node or agent code.
"""
