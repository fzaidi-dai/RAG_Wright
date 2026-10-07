"""ING-8d: the contracts pack's ingest knobs, set through `EngineOptions.packs["contracts"]` (moved out of the
generic `IngestOptions`). Every field defaults to `None` = the pack's env/default, so a caller who sets nothing
gets today's behavior."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

PACK_NAME = "contracts"


@dataclass(frozen=True)
class ContractIngestOptions:
    classify_concurrency: Optional[int] = None   # function-classify parallelism (env CLASSIFY_CONCURRENCY)
    clause_concurrency: Optional[int] = None     # clause-extraction parallelism (env CLAUSE_CONCURRENCY)
    affiliations: Optional[bool] = None          # run affiliation extraction (env RAG_INGEST_AFFILIATIONS)
    function_classifier: Optional[str] = None    # "setfit" | "llm" (env RAG_FUNCTION_CLASSIFIER)


def contract_ingest_options(config: Any) -> ContractIngestOptions:
    """This pack's options from an `EngineConfig` (its `options.packs["contracts"]`), else the defaults."""
    opts = config.options.packs.get(PACK_NAME)
    if opts is None:
        return ContractIngestOptions()
    if not isinstance(opts, ContractIngestOptions):
        raise TypeError(f'EngineOptions.packs["{PACK_NAME}"] must be a ContractIngestOptions, got {type(opts).__name__}')
    return opts
