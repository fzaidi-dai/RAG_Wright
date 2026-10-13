"""PS-38: the public-surface gaps a new product hits, closed (engine gaps G12, G14, G22, G23).

A product publishes its own capabilities' ARD manifests, fills a manifest's typed-interface and skill-runtime fields,
registers its own decision-model and chat-model profiles, and its pack reaches the workspace's embedder and config,
all through `rag_wright.api` / `rag_wright.pack_sdk`. The reference pack, the worked example products copy, reads no
private workspace attribute."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from rag_wright import api, pack_sdk
from rag_wright.models import profiles


def test_a_product_publishes_its_own_capability_to_an_ard_root(tmp_path, monkeypatch):
    from rag_wright.capabilities import manifests, registry
    monkeypatch.setattr(manifests, "MANIFEST_SPECS", {})  # this test's catalog only; the process catalog is untouched
    monkeypatch.setattr(registry, "_canonical_slugs", set(registry._canonical_slugs))
    api.register_canonical_slugs(["ps38_probe"])
    api.register_capability(api.CapabilityManifest(
        slug="ps38_probe", kind="function", display_name="PS-38 probe", description="A probe capability.",
        representative_queries=("probe the publish path", "check a product manifest")))
    path = api.publish("ps38_probe", root=tmp_path)
    assert path.parent == tmp_path
    assert json.loads(path.read_text())["envelope"]["identifier"].endswith(":ps38_probe")
    assert api.publish_all(root=tmp_path) == [tmp_path / "ps38_probe.json"]


def test_a_manifest_can_declare_its_interface_and_skill_runtime_from_the_pack_sdk():
    assert {"CapabilityInterface", "SkillRuntime"} <= set(pack_sdk.__all__)
    from rag_wright.capabilities.ard import CapabilityInterface, SkillRuntime
    assert pack_sdk.CapabilityInterface is CapabilityInterface and pack_sdk.SkillRuntime is SkillRuntime


def test_a_product_registers_its_own_decision_model(monkeypatch):
    monkeypatch.setattr(profiles, "DECISION_PROFILES", dict(profiles.DECISION_PROFILES))
    prof = api.DecisionModelProfile(model_id="laya-onprem", served_model_id="laya",
                                    endpoint="http://laya.local/v1/decisions", api_key_env="LAYA_KEY")
    assert api.register_decision_profile(prof) is prof
    assert pack_sdk.decision_profile("laya-onprem") is prof
    monkeypatch.setenv("RAG_DECISION_MODEL", "laya-onprem")
    assert pack_sdk.decision_profile().endpoint == "http://laya.local/v1/decisions"


def test_a_product_registers_its_own_chat_model_profile(monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", dict(profiles.PROFILES))
    prof = api.ModelProfile(model_id="acme-chat", backend="vllm", served_model_id="acme/chat",
                            base_url="http://chat.local/v1", api_key_env="ACME_KEY")
    api.register_model_profile(prof)
    assert profiles.profile_for("acme-chat") is prof


def test_a_pack_reaches_the_workspace_embedder_and_config(monkeypatch):
    from rag_wright.api import workspace as w
    config = api.EngineConfig(store=api.StoreConfig(host="h", port="1", user="u", password="p"))
    sentinel = object()
    monkeypatch.setattr(w, "_build_embedder", lambda cfg: sentinel)
    ws = w.WorkspaceHandle(store=object(), config=config, corpus="c")
    assert pack_sdk.workspace_embedder(ws) is sentinel
    assert ws.config is config
    with pytest.raises(AttributeError):
        ws.config = config  # read-only


def test_the_reference_pack_reads_no_private_workspace_attribute():
    root = Path(api.__file__).resolve().parent.parent / "packs"
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Attribute) and node.attr in {"_store", "_embedder", "_config", "_corpus"}
                    and isinstance(node.value, ast.Name) and node.value.id in {"resources", "ws", "handle"}):
                offenders.append(f"{path.relative_to(root)}:{node.lineno} {node.value.id}.{node.attr}")
    assert offenders == []
