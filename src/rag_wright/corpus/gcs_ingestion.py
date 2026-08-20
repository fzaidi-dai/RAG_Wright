"""PROD-1 (ADR-0049 generic-customer lens): a GCS `CorpusAdapter` — the first REAL source integration.

Where `CuadAdapter` reads a bundled dataset file, this reads a customer's documents straight from a Google Cloud
Storage prefix (`gs://<bucket>/<prefix>/`), the shape a real onboarding takes: the customer uploads their corpus
to a bucket, we ingest it. Everything downstream (`run_corpus_ingestion` + the per-document graph) is unchanged
and corpus-agnostic; only this adapter is GCS-specific.

Parse-by-extension: `.txt`/`.md`/`.text` are read directly (the PROD-1 corpus -- MAUD + ContractNLI -- is text);
binary customer docs (`.pdf`/`.docx`/`.html`) route through an injected `parse_bytes` seam (docling in production),
kept injectable so this adapter stays hermetically testable and so the doc-parse dependency is explicit, not
hidden. The google-cloud-storage client is injectable for the same reason (tests pass a fake; production builds a
real `storage.Client`).
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from rag_wright.subgraphs.contract_ingestion_pipeline import SourceDocument

_TEXT_EXTS = frozenset({"txt", "md", "text"})


class GcsCorpusAdapter:
    """`CorpusAdapter` over `gs://<bucket>/<prefix>`: list blobs under the prefix, read/parse each, yield one
    `SourceDocument` per document. `client` (a `google.cloud.storage.Client`) and `parse_bytes` (bytes->text for
    non-text blobs) are injected -- production builds them, tests fake them."""

    def __init__(
        self,
        bucket: str,
        prefix: str,
        *,
        limit: int = 0,
        include: Optional[frozenset] = None,
        client: Any = None,
        parse_bytes: Optional[Callable[[str, bytes], str]] = None,
        parse_doc: Optional[Callable[[str, str, bytes], SourceDocument]] = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        self._limit = limit
        self._include = include  # if set, only blobs whose BASENAME is in this set (a curated subset ingest)
        self._client = client
        self._parse_bytes = parse_bytes
        # CHUNK-7 (ADR-0058): structure-preserving seam for a non-text blob -- (source_doc_id, name, bytes) ->
        # a SourceDocument carrying `.parsed` (the real docling parse), so the chunker's structural pass fires.
        # Preferred over `parse_bytes` (text-only) when both are set; production injects it via the factory.
        self._parse_doc = parse_doc

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from google.cloud import storage  # lazy: keep the module import-light + hermetic

        return storage.Client()

    def _to_source(self, blob: Any, source_doc_id: str, meta: dict) -> Any:  # SourceDocument | PendingDocument | None
        """One blob -> a SourceDocument (or None to skip an empty object). A text blob is read as text; a non-text
        blob prefers the structure-preserving `parse_doc` seam (carries `.parsed`), else the `parse_bytes` text
        seam, else raises (no way to ingest a binary document without a parser)."""
        name = blob.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _TEXT_EXTS:
            text = blob.download_as_text()
            return SourceDocument(source_doc_id=source_doc_id, text=text, metadata=meta) if text.strip() else None
        if self._parse_doc is not None:  # 0009-ASYNC-INGEST: DEFER download+parse (incl. OCR/VLM escalation) so the
            from rag_wright.subgraphs.contract_ingestion_pipeline import PendingDocument  # ingest parses it
            _parse = self._parse_doc                                                       # concurrently + bounded
            return PendingDocument(source_doc_id=source_doc_id,
                                   parse=lambda: _parse(source_doc_id, name, blob.download_as_bytes()), metadata=meta)
        if self._parse_bytes is not None:  # legacy text-only seam (no structure)
            text = self._parse_bytes(name, blob.download_as_bytes())
            return SourceDocument(source_doc_id=source_doc_id, text=text, metadata=meta) if text.strip() else None
        raise NotImplementedError(
            f"non-text blob {name!r}: inject `parse_doc` (structure-preserving, the production default) or "
            f"`parse_bytes` (text) to ingest PDF/DOCX/HTML customer documents. The PROD-1 corpus is text (.txt).")

    def documents(self) -> Iterable[Any]:  # SourceDocument (text) or PendingDocument (binary, deferred parse)
        from rag_wright.contracts.identifiers import canonical_source_doc_id

        client = self._get_client()
        blobs = [b for b in client.list_blobs(self._bucket, prefix=self._prefix) if not b.name.endswith("/")]
        if self._include is not None:  # curated subset: keep only the named blobs (by basename)
            blobs = [b for b in blobs if b.name.rsplit("/", 1)[-1] in self._include]
        if self._limit:
            blobs = blobs[: self._limit]
        for blob in blobs:
            basename = blob.name.rsplit("/", 1)[-1]
            meta = {"source": "gcs", "bucket": self._bucket, "blob": blob.name}
            sd = self._to_source(blob, canonical_source_doc_id(basename), meta)
            if sd is not None:  # None -> empty/whitespace object skipped (a bad upload never dead-letters the run)
                yield sd


def production_gcs_adapter(bucket: str, prefix: str, *, limit: int = 0,
                          include: Optional[frozenset] = None, cache_dir: str = "data/cache/gcs_parse",
                          parse_bytes: Optional[Callable[[str, bytes], str]] = None) -> GcsCorpusAdapter:
    """Build the adapter with a real `google.cloud.storage.Client` (application-default credentials, the same auth
    gsutil uses). Lazy import so importing this module needs no GCS client. CHUNK-7 (ADR-0058): a non-text
    customer document (PDF/DOCX/HTML) is docling-parsed with its STRUCTURE PRESERVED (`.parsed`) so the chunker's
    structural pass fires -- no longer flattened to text. `parse_bytes` is a legacy text-only override."""
    from google.auth.exceptions import DefaultCredentialsError
    from google.cloud import storage

    try:
        client = storage.Client()
    except DefaultCredentialsError as e:  # actionable message: the python client needs ADC (gsutil uses gcloud auth)
        raise RuntimeError(
            "GCS python client needs Application Default Credentials. Run once: "
            "`gcloud auth application-default login` (or set GOOGLE_APPLICATION_CREDENTIALS to a service-account "
            "key in production). Note: gsutil/gcloud being authed is NOT sufficient for the python client.") from e
    parse_doc = None
    if parse_bytes is None:  # DOCPARSE-1 + CHUNK-7: structure-preserving docling parse for customer documents
        from rag_wright.subgraphs.contract_ingestion_pipeline import parsed_source_document

        parse_doc = lambda sid, name, data: parsed_source_document(  # noqa: E731
            sid, name, data, cache_dir=cache_dir)
    return GcsCorpusAdapter(bucket, prefix, limit=limit, include=include, client=client,
                            parse_bytes=parse_bytes, parse_doc=parse_doc)
