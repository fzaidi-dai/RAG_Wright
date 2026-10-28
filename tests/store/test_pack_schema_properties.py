"""PS-50 (TexWright's 2026-10-28 request): `ensure_pack_schema` reconciles PROPERTIES the way it reconciles types,
edge types and unique indexes: a property a pack gains is declared on a type the database already holds. A property
whose declared type moved is reported, never altered; a property the pack stopped declaring is never dropped."""
from __future__ import annotations

import pytest

from rag_wright.store.arcadedb import ArcadeDBStore

_DB = "ragwright_test_pack_schema_properties"


def _pack(tmp_path, name, *props):
    path = tmp_path / f"{name}.ttl"
    declared = ", ".join(f'"{p}"' for p in props)
    path.write_text("@prefix eng: <https://ragwright.local/ontology/engine#> .\n"
                    "@prefix t: <https://example.test/t#> .\n"
                    f't:RecNode a eng:KgVertexType ; eng:vertexName "Rec" ; eng:kgProperty {declared} ;\n'
                    '    eng:uniqueIndexOn "rec_id" .\n')
    return str(path)


def _declared(store, type_name):
    return {p["name"]: p["type"] for r in store._query("SELECT name, properties FROM schema:types")
            if r["name"] == type_name for p in r.get("properties", [])}


@pytest.mark.store
def test_a_property_a_pack_gains_is_declared_on_an_existing_type(tmp_path, caplog):
    s = ArcadeDBStore.from_env(database=_DB, reset=True)
    try:
        s.ensure_pack_schema(_pack(tmp_path, "v1", "rec_id:STRING", "name:STRING", "dropped:STRING"))
        s._command("INSERT INTO Rec SET rec_id = 'r1', name = 'a', weight = '195 GSM'")  # a re-ingest stored it early

        v2 = _pack(tmp_path, "v2", "rec_id:STRING", "name:INTEGER", "label:STRING", "weight:DOUBLE")
        with caplog.at_level("WARNING", logger="rag_wright.store.arcadedb"):
            s.ensure_pack_schema(v2)
        assert _declared(s, "Rec") == {"rec_id": "STRING", "name": "STRING", "dropped": "STRING",
                                       "label": "STRING", "weight": "DOUBLE"}  # added; never retyped or dropped
        warned = " ".join(r.getMessage() for r in caplog.records)
        assert "Rec.name" in warned and "STRING" in warned and "INTEGER" in warned  # the moved type is reported
        assert "Rec.weight" in warned and "1 existing" in warned  # values stored before the declaration: unconverted
        assert "Rec.label" not in warned  # a new STRING over no values needs no word

        caplog.clear()
        cmds = []
        run = s._command
        s._command = lambda sql: (cmds.append(sql), run(sql))[1]
        with caplog.at_level("WARNING", logger="rag_wright.store.arcadedb"):
            s.ensure_pack_schema(v2)
        assert cmds == []  # idempotent: nothing left to declare
    finally:
        s.drop()
        s.close()
