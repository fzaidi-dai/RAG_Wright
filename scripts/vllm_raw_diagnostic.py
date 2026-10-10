"""MODAL-STACK-2 diagnosis: WHY does vLLM-Granite extraction diverge from OpenRouter-Granite?
Send the SAME clause->JSON messages to both endpoints and dump the RAW completions -- to see if vLLM-Granite is
emitting REASONING ("Here's my thought process: ...") before the JSON (granite thinking mode), which would
degrade docling-graph's json parse and explain the under-extraction.

  VLLM_URL=https://...modal.run uv run python -m scripts.vllm_raw_diagnostic
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from dotenv import load_dotenv

_CLAUSE = ("In no event shall either party's aggregate liability arising out of or related to this Agreement "
           "exceed the total fees paid by Customer in the twelve (12) months preceding the claim, except for "
           "breaches of confidentiality, which shall be uncapped.")
_MSGS = [
    {"role": "system", "content": "Extract contract-clause fields as a compact JSON object with keys "
     "clause_type, liability_cap_amount, liability_cap_basis, carve_outs. Use null if absent. Output ONLY JSON."},
    {"role": "user", "content": f"Clause:\n{_CLAUSE}"},
]


def _call(base: str, key: str, model: str, json_mode: bool) -> dict:
    body = {"model": model, "messages": _MSGS, "temperature": 0, "max_tokens": 512}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=120))
    ch = r["choices"][0]
    return {"text": ch["message"]["content"], "finish": ch.get("finish_reason"),
            "out_tok": r.get("usage", {}).get("completion_tokens"), "dt": time.time() - t}


def _warm(base: str, key: str) -> None:
    for _ in range(120):
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/models", headers={"Authorization": f"Bearer {key}"}), timeout=5)
            return
        except Exception:  # noqa: BLE001
            time.sleep(5)


def _show(name: str, res: dict) -> None:
    txt = res["text"] or ""
    has_reason = any(s in txt for s in ("thought process", "Here is my response", "<think>", "my response:"))
    print(f"\n----- {name} (finish={res['finish']}, out_tok={res['out_tok']}, {res['dt']:.1f}s, "
          f"REASONING_TEXT={has_reason}) -----", flush=True)
    print(txt[:700], flush=True)


def main() -> None:
    load_dotenv()
    vbase = os.environ["VLLM_URL"].rstrip("/") + "/v1"
    vkey = os.environ["VLLM_API_KEY"]
    obase = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    okey = os.environ["OPENROUTER_API_KEY"]
    m = "ibm-granite/granite-4.1-8b"
    print("[diag] warming vLLM ...", flush=True)
    _warm(vbase, vkey)
    for json_mode in (True, False):
        print(f"\n========== json_object={json_mode} ==========", flush=True)
        _show("OpenRouter", _call(obase, okey, m, json_mode))
        _show("vLLM", _call(vbase, vkey, m, json_mode))


if __name__ == "__main__":
    main()
