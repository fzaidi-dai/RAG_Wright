"""The evaluation suite and golden question-and-answer set (SPEC.md §12).

Golden set split by the four archetypes. Measures retrieval recall at k per archetype
(each leg, text and graph, measured separately), the summary-miss failure mode,
chunk-boundary quality and summary fidelity (A/B the RLM chunker against a baseline),
entity-resolution quality, end-to-end answer quality, faithfulness, citation correctness,
latency and its tail, and the per-source ablation.
"""
