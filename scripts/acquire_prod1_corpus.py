"""PROD-1 (production-readiness): acquire a small, diverse, REAL non-CUAD contract corpus for the end-to-end
ingest test. Two independent, freely-licensed (CC BY 4.0) sources, both FULL documents (not span-level):

  - MAUD (theatticusproject/maud, HF repo files): raw full MERGER AGREEMENTS -> exercises condition_type/MAC,
    dispute_method, termination.
  - ContractNLI (presencesw/contract-nli, HF): full NDAs (dedup sentence1) -> exercises Confidentiality +
    confidentiality_exception.

Writes plain .txt files to data/prod1_corpus/ (gitignored data artifact); scripts/upload then puts them on GCS.

  MAUD_N=50 NLI_N=50 uv run --no-sync python -m scripts.acquire_prod1_corpus
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def log(m: str) -> None:
    print(m, flush=True)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def main() -> None:
    maud_n = int(os.environ.get("MAUD_N", "50"))
    nli_n = int(os.environ.get("NLI_N", "50"))
    out = Path("data/prod1_corpus")
    out.mkdir(parents=True, exist_ok=True)

    # --- MAUD: raw full merger agreements (repo .txt files) ---
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    files = api.list_repo_files("theatticusproject/maud", repo_type="dataset")
    contract_files = sorted(f for f in files if f.startswith("MAUD_v1/contracts/") and f.endswith(".txt"))
    picked = contract_files[:maud_n]
    log(f"[acquire] MAUD: downloading {len(picked)}/{len(contract_files)} merger agreements ...")
    for i, f in enumerate(picked, 1):
        local = hf_hub_download("theatticusproject/maud", f, repo_type="dataset")
        text = Path(local).read_text(encoding="utf-8", errors="replace")
        name = Path(f).stem  # contract_N
        (out / f"maud_{name}.txt").write_text(text, encoding="utf-8")
        if i % 10 == 0 or i == len(picked):
            log(f"[acquire] MAUD {i}/{len(picked)}")

    # --- ContractNLI: full NDAs (dedup the repeated sentence1 document) ---
    from datasets import load_dataset

    seen: set[str] = set()
    written = 0
    log(f"[acquire] ContractNLI: selecting {nli_n} unique full NDAs ...")
    for split in ("train", "test"):
        for r in load_dataset("presencesw/contract-nli", split=split):
            doc = (r.get("sentence1") or "").strip()
            if len(doc) < 500 or doc in seen:  # skip fragments / dups
                continue
            seen.add(doc)
            written += 1
            (out / f"contractnli_nda_{written:03d}.txt").write_text(doc, encoding="utf-8")
            if written % 10 == 0 or written == nli_n:
                log(f"[acquire] ContractNLI {written}/{nli_n}")
            if written >= nli_n:
                break
        if written >= nli_n:
            break

    total = len(list(out.glob("*.txt")))
    log(f"\n[acquire] DONE: {total} contracts in {out}/ "
        f"(MAUD {len(picked)} merger agreements + ContractNLI {written} NDAs). "
        f"Next: upload to GCS gs://.../prod1-corpus/, then the GcsCorpusAdapter.")


if __name__ == "__main__":
    main()
