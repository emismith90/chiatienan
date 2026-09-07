from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text

from kernos.content import BindingOverrides, Models, ProfileSpec, Prompt, Runtime
from kernos.content.models import Base
from kernos.content.schema import bind, sync_additive_columns

TABLES = {"kn_businesses", "kn_profiles", "kn_profile_versions", "kn_sources", "kn_agents",
          "kn_space_bindings", "kn_model_catalogue", "kn_audit_log"}


def test_bind_creates_every_table_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/c.db", future=True)
    bind(engine)
    assert TABLES <= set(inspect(engine).get_table_names())
    bind(engine)                                            # no-op
    assert TABLES <= set(inspect(engine).get_table_names())
    assert Base.metadata.sorted_tables                       # no FK cycle: sorted_tables resolves


def test_sync_adds_a_missing_column_with_its_default(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
    md = MetaData()
    Table("t", md, Column("id", Integer, primary_key=True),
          Column("flag", Integer, default=7, nullable=False), Column("note", String(10)))
    assert sync_additive_columns(engine, md) == ["t.flag", "t.note"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t (id) VALUES (1)"))
        assert conn.execute(text("SELECT flag FROM t")).scalar() == 7
    assert sync_additive_columns(engine, md) == []


def test_specs_are_frozen_and_overrides_copy():
    spec = ProfileSpec(models=Models(text="m"), prompt=Prompt(body="B", append=["a"]),
                       runtime=Runtime(cwd="/c", agent_dir="/a"))
    try:
        spec.prompt = Prompt(body="X")
        raise AssertionError("frozen model accepted assignment")
    except Exception as exc:  # pydantic ValidationError (frozen)
        assert "frozen" in str(exc).lower()
    out = BindingOverrides(append_sections=["room rule"], handle="lunchbot").apply(spec)
    assert out.prompt.append == ["a", "room rule"] and out.persona.handle == "lunchbot"
    assert spec.prompt.append == ["a"] and spec.persona.handle == "assistant"   # untouched
    assert BindingOverrides().apply(spec) is spec
    assert "runtime" not in spec.stored() and spec.with_runtime(Runtime(cwd="/x", agent_dir="/y")).runtime.cwd == "/x"
