"""PS-1 (G17): `ModelRole` is public. The public API needs it (`WorkspaceHandle.model_id(role)`, `EngineConfig.models`
keyed by its values), so a product names a role through `rag_wright.api`, never `rag_wright.models.profiles`."""
from __future__ import annotations

import rag_wright.models.profiles as profiles
from rag_wright import api


def test_model_role_is_exported_and_is_the_engine_enum():
    assert "ModelRole" in api.__all__
    assert api.ModelRole is profiles.ModelRole  # the same object, not a fork


def test_a_product_overrides_and_reads_a_role_through_the_public_api():
    from rag_wright.api import EngineConfig, ModelRole, StoreConfig, WorkspaceHandle

    cfg = EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p"),
                       models={ModelRole.SUMMARIZATION.value: "my-small-model"})
    ws = WorkspaceHandle(store=object(), config=cfg, corpus="c")
    assert ws.model_id(ModelRole.SUMMARIZATION) == "my-small-model"  # the override wins
    assert ws.model_id(ModelRole.GENERAL)  # an unset role resolves to the profile default
