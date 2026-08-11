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
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        self._limit = limit
        self._include = include  # if set, only blobs whose BASENAME is in this set (a curated subset ingest)
        self._client = client
        self._parse_bytes = parse_bytes

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from google.cloud import storage  # lazy: keep the module import-light + hermetic

        return storage.Client()

    def _to_text(self, blob: Any) -> str:
        name = blob.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _TEXT_EXTS:
            return blob.download_as_text()
        if self._parse_bytes is None:
            raise NotImplementedError(
                f"non-text blob {name!r}: inject `parse_bytes` (docling) to ingest PDF/DOCX/HTML "
                f"customer documents. The PROD-1 corpus is text (.txt).")
        return self._parse_bytes(name, blob.download_as_bytes())

    def documents(self) -> Iterable[SourceDocument]:
        from rag_wright.contracts.identifiers import canonical_source_doc_id

        client = self._get_client()
        blobs = [b for b in client.list_blobs(self._bucket, prefix=self._prefix) if not b.name.endswith("/")]
        if self._include is not None:  # curated subset: keep only the named blobs (by basename)
            blobs = [b for b in blobs if b.name.rsplit("/", 1)[-1] in self._include]
        if self._limit:
            blobs = blobs[: self._limit]
        for blob in blobs:
            text = self._to_text(blob)
            if not text or not text.strip():
                continue  # empty / whitespace-only object -> skip (a bad upload never dead-letters the run)
            basename = blob.name.rsplit("/", 1)[-1]
            yield SourceDocument(
                source_doc_id=canonical_source_doc_id(basename),
                text=text,
                metadata={"source": "gcs", "bucket": self._bucket, "blob": blob.name},
            )


def production_gcs_adapter(bucket: str, prefix: str, *, limit: int = 0,
                          include: Optional[frozenset] = None,
                          parse_bytes: Optional[Callable[[str, bytes], str]] = None) -> GcsCorpusAdapter:
    """Build the adapter with a real `google.cloud.storage.Client` (application-default credentials, the same auth
    gsutil uses). Lazy import so importing this module needs no GCS client."""
    from google.auth.exceptions import DefaultCredentialsError
    from google.cloud import storage

    try:
        client = storage.Client()
    except DefaultCredentialsError as e:  # actionable message: the python client needs ADC (gsutil uses gcloud auth)
        raise RuntimeError(
            "GCS python client needs Application Default Credentials. Run once: "
            "`gcloud auth application-default login` (or set GOOGLE_APPLICATION_CREDENTIALS to a service-account "
            "key in production). Note: gsutil/gcloud being authed is NOT sufficient for the python client.") from e
    if parse_bytes is None:  # DOCPARSE-1: default to the generic docling parser so PDF/DOCX customer contracts ingest
        from rag_wright.corpus.document_parser import document_to_text, parse_document_bytes

        parse_bytes = lambda name, data: document_to_text(parse_document_bytes(name, data))  # noqa: E731
    return GcsCorpusAdapter(bucket, prefix, limit=limit, include=include, client=client, parse_bytes=parse_bytes)
