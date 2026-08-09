#!/usr/bin/env bash
# Back up an ArcadeDB database straight to GCS (not local disk), so local/container disk does not fill with
# redundant KG backups. Runs BACKUP DATABASE, streams the resulting zip container->GCS (no host temp file),
# verifies it landed, then deletes the local container copy.
#
#   bash scripts/backup_kg_to_gcs.sh <database>            # e.g. ragwright_cuad_full
#
# Env (defaults for this project):
#   ARCADEDB_CONTAINER   docker container running ArcadeDB   (default: arcadedb-ragwright)
#   ARCADEDB_HTTP        ArcadeDB HTTP endpoint              (default: http://localhost:2480)
#   ARCADEDB_USER        (default: root)   ARCADEDB_PASSWORD (read from .env if unset)
#   KG_BACKUP_BUCKET     GCS prefix                          (default: gs://dreamai-pocs-ragwright-ingest/kg-backups)
set -euo pipefail

DB="${1:?usage: backup_kg_to_gcs.sh <database>}"
CONTAINER="${ARCADEDB_CONTAINER:-arcadedb-ragwright}"
HTTP="${ARCADEDB_HTTP:-http://localhost:2480}"
USER="${ARCADEDB_USER:-root}"
BUCKET="${KG_BACKUP_BUCKET:-gs://dreamai-pocs-ragwright-ingest/kg-backups}"
# ARCADEDB_PASSWORD from env, else .env
if [ -z "${ARCADEDB_PASSWORD:-}" ] && [ -f .env ]; then
  ARCADEDB_PASSWORD="$(grep -E '^ARCADEDB_PASSWORD=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"'')"
fi

echo "[backup] BACKUP DATABASE ${DB} ..."
RESP="$(curl -s -X POST "${HTTP}/api/v1/command/${DB}" -u "${USER}:${ARCADEDB_PASSWORD}" \
  -H "Content-Type: application/json" -d '{"command":"BACKUP DATABASE","language":"sql"}')"
ZIP="$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"][0]["backupFile"])')"
[ -n "$ZIP" ] || { echo "[backup] FAILED to get backupFile from: $RESP" >&2; exit 1; }
SRC="/home/arcadedb/backups/${DB}/${ZIP}"
DST="${BUCKET}/${ZIP}"

echo "[backup] streaming ${ZIP} (container ${SRC}) -> ${DST} ..."
docker exec "${CONTAINER}" cat "${SRC}" | gsutil -q cp - "${DST}"
gsutil ls -l "${DST}"

echo "[backup] deleting local container copy ${SRC}"
docker exec "${CONTAINER}" rm -f "${SRC}"
echo "[backup] done: ${DST}"
