"""PS-7 (G15): `pack_store(ws, cls, *args, **kwargs)` is the one public way to build a pack store extension (or any
object wrapping the workspace store) -- `ws._store` stays private, and nothing a product copies from uses it."""
from __future__ import annotations

from pathlib import Path

from rag_wright.api import EngineConfig, StoreConfig, pack_store
from rag_wright.api.workspace import WorkspaceHandle

_STORE = object()


def _ws():
    return WorkspaceHandle(_STORE, EngineConfig(store=StoreConfig("localhost", 2480, "u", "p")), "t")


class _PackStore:
    def __init__(self, store, checkpoint_dir=None):
        self.store, self.checkpoint_dir = store, checkpoint_dir


def test_pack_store_wraps_the_workspace_store_and_forwards_arguments():
    ps = pack_store(_ws(), _PackStore, checkpoint_dir="/tmp/ck")
    assert isinstance(ps, _PackStore) and ps.store is _STORE and ps.checkpoint_dir == "/tmp/ck"


def test_the_reference_seam_builds_its_pack_stores_through_pack_store(monkeypatch):
    import rag_wright.packs.reference_seam as seam_mod

    built = []

    class _FakeContracts:
        def __init__(self, store):
            built.append(store)

        def contract_terms(self, contract_id):
            return [contract_id]

    monkeypatch.setattr(seam_mod, "ContractKGStore", _FakeContracts)
    monkeypatch.setattr(seam_mod, "load_reference_pack", lambda: None)
    assert seam_mod.ContractComplianceSeam(config=object()).contract_terms(_ws(), "C1") == ["C1"]
    assert built == [_STORE]


def test_nothing_a_product_copies_from_reaches_ws_store():
    root = Path(__file__).resolve().parents[2]
    files = [*(root / "src/rag_wright/packs").rglob("*.py"), *(root / "docs/domain-adaptation").glob("*.md"),
             *(root / "docs/templates").rglob("*"), *(root / ".claude/skills").rglob("*.md"),
             root / "docs/product/seam-adaptation-guide.md"]
    offenders = [str(f.relative_to(root)) for f in files
                 if f.is_file() and f.name != "_engine-gaps.md" and "ws._store" in f.read_text(errors="ignore")]
    assert offenders == []
