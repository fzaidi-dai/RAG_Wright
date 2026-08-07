"""A1 / ADR-0042: apply the clause-span_id backfill on the Modal KG CO-LOCATED (from inside a Modal container),
not laptop-drives-remote.

The direct https run from the laptop was latency-bound AND unreliable (Modal-http dropped the long sequential
session), the [[gcp-bulk-ingestion-box]] lesson. Instead this job mounts the `rw-arcadedb-data` Volume, starts
ArcadeDB against it on localhost, runs the (proven, deterministic) backfill over localhost (no https proxy),
gracefully shuts ArcadeDB down to flush, and commits the Volume.

SINGLE-WRITER: run with `rw-arcadedb` STOPPED (one ArcadeDB per Volume), then redeploy `rw-arcadedb` after.

  uv run --no-sync modal app stop --yes rw-arcadedb
  uv run --no-sync modal run scripts/modal_backfill.py                 # apply
  uv run --no-sync modal run scripts/modal_backfill.py --dry-run       # match-rate only, no writes
  uv run --no-sync modal deploy scripts/modal_arcadedb.py              # bring the serving KG back
"""

import modal

ROOT_PW = "rag_wright_dev_2026"
ARCADE_HOME = "/home/arcadedb"
DB_MOUNT = f"{ARCADE_HOME}/databases"
DB_NAME = "ragwright_cuad_full"
_TARBALL = "https://github.com/ArcadeData/arcadedb/releases/download/26.7.1/arcadedb-26.7.1.tar.gz"

app = modal.App("rw-backfill")
data_vol = modal.Volume.from_name("rw-arcadedb-data")
image = (
    # Java 21 (to run ArcadeDB) + python (to run the backfill against localhost). The store's import path only
    # needs arcadedb-python + pydantic + the docling-graph/pyshacl/langgraph deps rag_wright.contracts pulls in.
    modal.Image.from_registry("eclipse-temurin:21-jre", add_python="3.12")
    .apt_install("curl")
    .run_commands(f"curl -sL {_TARBALL} -o /opt/arcadedb.tgz")
    .pip_install(
        "langgraph", "langchain-openai>=1.3.3", "arcadedb-python>=0.4.0",
        "docling-graph[templategen]==1.9.1", "pyshacl>=0.40.1", "pydantic>=2.0", "httpx", "python-dotenv",
    )
    .env({
        "JAVA_OPTS": f"-Darcadedb.server.rootPassword={ROOT_PW} -Darcadedb.server.mode=development",
        "ARCADEDB_OPTS_MEMORY": "-Xms2G -Xmx6G",
    })
    .add_local_dir("scripts", remote_path="/root/scripts")  # the backfill script (scripts is not a package)
    .add_local_python_source("rag_wright")  # MUST be last
)


@app.function(image=image, volumes={DB_MOUNT: data_vol}, timeout=3600)
def run_backfill(dry_run: bool = False) -> None:
    import base64
    import os
    import shutil
    import signal
    import subprocess
    import sys
    import tarfile
    import time
    import urllib.request

    data_vol.reload()
    # 1. install ArcadeDB into the ephemeral FS (extract; never clobber the Volume-mounted databases dir)
    if not os.path.exists(f"{ARCADE_HOME}/bin/server.sh"):
        os.makedirs(ARCADE_HOME, exist_ok=True)
        with tarfile.open("/opt/arcadedb.tgz") as t:
            t.extractall("/tmp/adb")
        inner = os.path.join("/tmp/adb", os.listdir("/tmp/adb")[0])
        for name in os.listdir(inner):
            dst = os.path.join(ARCADE_HOME, name)
            if not os.path.exists(dst):
                shutil.move(os.path.join(inner, name), dst)

    server = subprocess.Popen(["./bin/server.sh"], cwd=ARCADE_HOME)

    # 2. wait until the DB answers (opens on first command)
    auth = base64.b64encode(f"root:{ROOT_PW}".encode()).decode()
    body = b'{"language":"sql","command":"SELECT count(*) as n FROM Contract"}'
    ready = False
    for _ in range(120):
        try:
            req = urllib.request.Request(
                f"http://localhost:2480/api/v1/command/{DB_NAME}", data=body, method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
            urllib.request.urlopen(req, timeout=4).read()
            ready = True
            break
        except Exception:  # noqa: BLE001 - server still starting / db not open yet
            time.sleep(2)
    if not ready:
        raise RuntimeError("ArcadeDB did not become ready on localhost:2480")
    print("[modal-backfill] ArcadeDB ready on localhost; running backfill (dry_run=%s)" % dry_run, flush=True)

    # 3. run the proven backfill over localhost (no https proxy, no laptop RTT)
    os.environ.update({
        "ARCADEDB_HOST": "localhost", "ARCADEDB_PORT": "2480", "ARCADEDB_PROTOCOL": "http",
        "ARCADEDB_USER": "root", "ARCADEDB_PASSWORD": ROOT_PW, "ARCADEDB_DATABASE": DB_NAME})
    if dry_run:
        os.environ["DRY_RUN"] = "1"
    sys.path.insert(0, "/root/scripts")
    import backfill_clause_span_id

    backfill_clause_span_id.main()

    # 4. graceful shutdown (flush) then persist the Volume
    server.send_signal(signal.SIGTERM)
    try:
        server.wait(timeout=180)
    except Exception:  # noqa: BLE001
        server.kill()
    if not dry_run:
        data_vol.commit()
        print("[modal-backfill] Volume committed", flush=True)


@app.local_entrypoint()
def main(dry_run: bool = False) -> None:
    run_backfill.remote(dry_run=dry_run)
