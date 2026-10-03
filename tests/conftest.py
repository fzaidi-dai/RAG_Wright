"""EP-CORE-3 (ADR-0118): the engine ships an EMPTY ARD catalog -- a developer registers their product's
capabilities at runtime. The ENGINE's own test suite, however, treats the contract/compliance pack as its worked
domain, so we load the reference pack here, at conftest IMPORT time (before any test module is collected -- some
tests parametrize over `MANIFEST_SPECS` at collection). A downstream product would NOT do this; it registers its own.
"""
from rag_wright.capabilities.manifests import load_reference_pack

load_reference_pack()
