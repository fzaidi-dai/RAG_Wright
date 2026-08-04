"""EC-1 (ENTERPRISE-CONTAINER, ADR-0039): ArcadeDB serving the adopted KG on Modal, Volume-backed + ALWAYS-WARM.

The live contract KG (`ragwright_cuad_full`, incl. the PARTY-TO-MANY-TO-MANY 1278 edges) lives on the
`rw-arcadedb-data` Modal Volume (the restored backup, unpacked once on first boot). ArcadeDB is a pure-Java
server; the stock `arcadedata/arcadedb` image is Alpine (musl) which Modal's Python runner can't run on, so we
build a debian + JRE image and drop the ArcadeDB 26.7.1 distribution into it, then run the server against the
Volume-mounted databases dir and expose the HTTP API (2480). `min_containers=1` keeps it always warm (cheap
CPU, no per-query DB cold start -- the EC decision); the A100 is the scale-to-zero piece.

Auth = the ArcadeDB root password (basic auth on every request), same posture as the vLLM app's api-key.

  uv run --no-sync modal deploy scripts/modal_arcadedb.py     # -> a stable https URL (the ArcadeDB HTTP API)
  uv run --no-sync modal app stop rw-arcadedb                 # tear down
"""

import os
import shutil
import subprocess
import tarfile
import zipfile

import modal

ROOT_PW = "rag_wright_dev_2026"  # matches the local ARCADEDB_PASSWORD (dev); the query app uses it as basic auth
ARCADE_HOME = "/home/arcadedb"
DB_MOUNT = f"{ARCADE_HOME}/databases"  # ArcadeDB's default databases dir
DB_NAME = "ragwright_cuad_full"
BACKUP_ZIP = f"{DB_MOUNT}/{DB_NAME}-backup.zip"  # the uploaded consistent backup (single-file, robust upload)
_TARBALL = "https://github.com/ArcadeData/arcadedb/releases/download/26.7.1/arcadedb-26.7.1.tar.gz"

app = modal.App("rw-arcadedb")
data_vol = modal.Volume.from_name("rw-arcadedb-data", create_if_missing=True)
image = (
    # ArcadeDB 26.7.1 is compiled for Java 21 (class v65); Temurin 21-jre is Ubuntu/glibc so add_python works
    modal.Image.from_registry("eclipse-temurin:21-jre", add_python="3.12")
    .apt_install("curl")
    # download the distribution at build; EXTRACT at runtime (the build sandbox FS can't recreate its hardlinks)
    .run_commands(f"curl -sL {_TARBALL} -o /opt/arcadedb.tgz")
    .env({
        "JAVA_OPTS": f"-Darcadedb.server.rootPassword={ROOT_PW} -Darcadedb.server.mode=development",
        "ARCADEDB_OPTS_MEMORY": "-Xms2G -Xmx6G",
    })
)


@app.function(image=image, volumes={DB_MOUNT: data_vol}, timeout=86400,
              min_containers=1, scaledown_window=3600)
@modal.web_server(port=2480, startup_timeout=900)
def serve() -> None:
    """Install ArcadeDB into the ephemeral FS (extract works here, unlike the build sandbox), restore the db
    from the uploaded backup zip once into the Volume, then start the server."""
    data_vol.reload()
    # 1. unpack the ArcadeDB distribution alongside the databases mount (skip the mount itself)
    if not os.path.exists(f"{ARCADE_HOME}/bin/server.sh"):
        os.makedirs(ARCADE_HOME, exist_ok=True)
        with tarfile.open("/opt/arcadedb.tgz") as t:
            t.extractall("/tmp/adb")
        inner = os.path.join("/tmp/adb", os.listdir("/tmp/adb")[0])  # the arcadedb-<version> top dir
        for name in os.listdir(inner):
            dst = os.path.join(ARCADE_HOME, name)
            if not os.path.exists(dst):  # never clobber the Volume-mounted databases/ dir
                shutil.move(os.path.join(inner, name), dst)
    # 2. restore the db from the backup zip (once) into the Volume
    db_dir = f"{DB_MOUNT}/{DB_NAME}"
    if not os.path.exists(db_dir) and os.path.exists(BACKUP_ZIP):
        os.makedirs(db_dir, exist_ok=True)
        with zipfile.ZipFile(BACKUP_ZIP) as z:
            z.extractall(db_dir)  # the backup zip is the raw db files (file-level snapshot)
        data_vol.commit()
    subprocess.Popen(["./bin/server.sh"], cwd=ARCADE_HOME)
