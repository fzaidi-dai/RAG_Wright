# ADR-0038: The full 506-contract CUAD KG is built on a GCP GPU VM and backed up to GCS

Date: 2026-08-03
Status: Accepted

## Context

CUAD-FULL-COVERAGE required ingesting all 510 CUAD contracts uniformly (every stage: clause KG,
entity graph, dense/sparse Span index, party↔contract link). The local Dockerized ArcadeDB kept
OOM-ing on the BGE-M3 vector-index build even at a 6 GB JVM heap (see ADR-0037 context / memory
`arcadedb-container-heap`), because the Docker Desktop VM is capped at 7.65 GiB while the build needs
more. Running the ingest laptop→remote was infeasible (SSH-tunnel RTT ~713 ms × ~176K writes ≈ 35 hr).

## Decision

**Build the KG co-located on a GCP GPU VM, then back it up as a portable snapshot in GCS.**

- **Compute:** GCP VM `arcadedb-gpu` (g2-standard-8, NVIDIA **L4**, us-central1-a). ArcadeDB runs in
  Docker on the VM (`-Xmx16G`); the whole ingest runs on the VM so writes are localhost. BGE-M3 **and**
  LegalBERT run on the L4 (`EMBED_DEVICE=cuda`, the device-resolve fix). Environment shipped via
  `git archive` (code) + a GCS bucket (corpus + clause cache + LegalBERT model). Details + the
  repeatable recipe: memory `gcp-bulk-ingestion-box`.
- **Backup method (how, exactly):** ArcadeDB's **consistent online `BACKUP DATABASE`**, triggered over
  the HTTP command API against `ragwright_cuad_full`:
  `POST /api/v1/command/ragwright_cuad_full  {"language":"sql","command":"BACKUP DATABASE"}`.
  It writes `ragwright_cuad_full-backup-<ts>.zip` into the container's `/home/arcadedb/backups/<db>/`
  dir. We `docker cp` that zip out of the container and `gcloud storage cp` it to the bucket. Online +
  consistent, so no need to stop the DB.
- **Backup location:** `gs://dreamai-pocs-ragwright-ingest/kg-backups/ragwright_cuad_full-backup-20260802-202058017.zip`
  (**627 MB**, compressed from ~1.5 GB). The `kg-backups/` folder in the same bucket used to stage the
  ingest inputs.
- **Restore:** ArcadeDB `RESTORE DATABASE file://<zip>` — anywhere (a fresh VM, a bigger local heap, a
  future prod host). The zip is the self-contained unit: graph + clause KG + entity graph + the BGE
  dense/sparse Span vectors all live inside the one ArcadeDB database.

## Consequences

- **The KG is durable and portable**, independent of the ephemeral GPU VM. Losing/stopping the VM loses
  nothing — restore from the zip. (The clause cache in GCS is a second, extraction-level backup: the KG
  is also fully reproducible from it by re-running the pipeline, extraction free.)
- **Coverage achieved (verified on the VM):** 506/510 contracts, uniform across every stage —
  Contract 506 · Clause 42,269 · PropertyValue 9,013 · **Span 136,292 (with embeddings)** · Entity 1,174
  · PartyTo 1,174 · 72,664 typed clause edges (25 edge types). The original inconsistency
  (Contract 102 / clause KG ~100 / Span 77 / entity ~482) is fixed. The **4 missing** are short one-page
  filings that failed semantic chunking (`chunk/ingest_failed`); recoverable with a "too-short → single
  chunk" fallback + a `RESET=0` re-ingest of just those 4.
- **Adoption is deferred** (user, after-a-break): the query pipeline will point at this KG later — either
  remotely (restart the VM + SSH tunnel) or by restoring the GCS backup into a local ArcadeDB. Not yet
  the live `ragwright_cuad`; that swap awaits the adoption decision.
- The GPU VM is **stopped** after backup to halt billing; restart or restore from GCS when adoption is
  decided.

See ADR-0037 (clause template authoritative code), memory `gcp-bulk-ingestion-box`,
`arcadedb-container-heap`, `local-vs-hosted-granite-throughput`.
