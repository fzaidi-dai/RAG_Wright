"""PS-35: a product can migrate a pre-0.2.0 database's `Span` fields through `rag_wright.api`. The migration was
reachable only through `scripts/migrate_span_fields.py`, which is not in the wheel, while `open_workspace` refused
the unmigrated database and told the product to run that script. `migrate_span_fields(config, corpus=...)` opens
the store WITHOUT the schema check (that check is what refuses it), migrates, and then runs the check as its
post-condition."""
from __future__ import annotations

import pytest

from rag_wright.api import EngineConfig, StoreConfig, migrate_span_fields
from rag_wright.api import workspace as w

CONFIG = EngineConfig(store=StoreConfig(host="h", port="1", user="u", password="p"))


class _Store:
    def __init__(self, pending):
        self.pending, self.calls = pending, []

    def migrate_span_fields(self, *, batch, progress):
        self.calls.append(("migrate", batch))
        moved, self.pending = self.pending, 0
        if progress is not None and moved:
            progress(moved, moved)
        return moved

    def ensure_schema(self):
        self.calls.append(("ensure_schema",))
        if self.pending:
            raise RuntimeError("still unmigrated")

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def built(monkeypatch):
    state = {"stores": [], "create": []}

    def build_store(config, corpus, *, reset, create=True):
        assert reset is False
        state["create"].append(create)
        state["stores"].append(_Store(pending=3))
        return state["stores"][-1]

    monkeypatch.setattr(w, "_build_store", build_store)
    return state


def test_it_migrates_then_checks_the_schema(built):
    seen = []
    assert migrate_span_fields(CONFIG, corpus="old", batch=2, progress=lambda d, t: seen.append((d, t))) == 3
    assert built["stores"][0].calls == [("migrate", 2), ("ensure_schema",), ("close",)]
    assert seen == [(3, 3)]


def test_it_never_creates_a_database(built):
    # a wrong name must fail loudly, not provision an empty database and report "0 moved"
    migrate_span_fields(CONFIG, corpus="old")
    assert built["create"] == [False]


@pytest.mark.parametrize("corpus", ["", "   ", None])
def test_it_refuses_a_missing_or_blank_name(built, corpus):
    with pytest.raises(ValueError):
        migrate_span_fields(CONFIG, corpus=corpus)
    assert built["stores"] == []
