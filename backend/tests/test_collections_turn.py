"""The generated collection tools end to end (plan Task 5.2)."""
import json
import subprocess
from pathlib import Path

import pytest

import app.agent as agent_mod
from app import chat
from app.agent import ToolInvocation, TurnResult
from app.kernel import kernel_for
from app.tools import ToolContext, build_tools, tool_manifest
from kernos.content.errors import GateError
from kernos.packs import PackError
from tests.test_ledger import _seed_room

ROTA = {"type": "object", "required": ["week", "who"],
        "properties": {"week": {"type": "string", "description": "ISO week"}, "who": {"type": "string"},
                       "brings": {"type": "string", "enum": ["cards", "chips"]}, "players": {"type": "integer"}}}
SIDECAR = Path(__file__).resolve().parent.parent / "agent_sidecar"


def _setup(db, n=2):
    room_id, m = _seed_room(db, n)
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    k.data.put_collection(bid, "rota", name="Card rota", description="who brings what", schema=ROTA,
                          key="week", indexed=["who"], actor="admin", reserved=k.reserved_tool_names())
    return room_id, m, k, bid


def _publish_with_collections(k):
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    packs = k.store.get_version(d["id"])["spec"]["tool_packs"] + [{"pack": "collections"}]
    k.store.update_draft(d["id"], {"tool_packs": packs}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")


def test_generated_tools_follow_the_lunch_tools_and_convert_in_the_sidecar(db):
    room_id, m, k, bid = _setup(db)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0],
                      tool_config={"packs": [{"pack": "lunch_ledger"}, {"pack": "ledger_tools"}, {"pack": "room_members"},
                                             {"pack": "lunch_places"}, {"pack": "collections"}]})
    names = [t["name"] for t in tool_manifest(ctx)]
    assert names[:19] == [t["name"] for t in tool_manifest()] and names[19:] == ["rota_find", "rota_upsert", "rota_delete"]
    tools = build_tools(ctx)
    assert "who brings what" in tools["rota_find"].description and "never a count" in tools["rota_find"].description
    assert tools["rota_upsert"].input_schema["properties"]["data"] == ROTA
    assert tools["rota_find"].input_schema["properties"]["where"]["properties"] == {"who": {"type": "string"}}
    # the sidecar's own converter accepts the whole manifest
    script = ('import { toTypeBoxManifest } from "./schema.js"; let s=""; process.stdin.on("data", d => s += d);'
              'process.stdin.on("end", () => { const out = toTypeBoxManifest(JSON.parse(s)); console.log(Object.keys(out).length); });')
    manifest = {t["name"]: t["schema"] for t in tool_manifest(ctx)}
    run = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(manifest),
                         capture_output=True, text=True, cwd=SIDECAR, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == str(len(manifest))
    # another space of the same business sees the same tools; an unrelated slug clash is refused
    with pytest.raises(Exception, match="rota_find"):
        k.data.put_collection(bid, "rota", name="x", schema=ROTA, key="week", actor="admin", reserved={"rota_find"})


def test_the_tools_validate_write_find_and_delete(db):
    room_id, m, k, bid = _setup(db)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0], tool_config={"packs": [{"pack": "collections"}]})
    tools = build_tools(ctx)
    bad = tools["rota_upsert"].execute({"data": {"week": "2026-W36", "who": "An", "brings": "beer"}})
    assert bad["ok"] is False and "brings" in bad["error"]
    assert tools["rota_upsert"].execute({"data": "nope"})["ok"] is False
    col = k.data.get_collection(bid, "rota")
    assert k.data.find_documents(col, room_id)["documents"] == []
    ok = tools["rota_upsert"].execute({"data": {"week": "2026-W36", "who": "An", "brings": "cards", "players": 6}})
    assert ok == {"ok": True, "type": "rota_document", "collection": "rota", "doc_id": "2026-W36",
                  "data": {"week": "2026-W36", "who": "An", "brings": "cards", "players": 6}}
    assert k.data.get_document(col, room_id, "2026-W36")["created_by"] == str(m[0])
    tools["rota_upsert"].execute({"data": {"week": "2026-W37", "who": "Binh"}})
    found = tools["rota_find"].execute({"where": {"who": "An"}})
    assert found["ok"] and [d["doc_id"] for d in found["documents"]] == ["2026-W36"] and found["more"] is False
    assert [d["doc_id"] for d in tools["rota_find"].execute({})["documents"]] == ["2026-W36", "2026-W37"]
    assert tools["rota_find"].execute({"where": {"players": 6}})["ok"] is False
    assert tools["rota_find"].execute({"where": "x"})["ok"] is False
    gone = tools["rota_delete"].execute({"doc_id": "2026-W37"})
    assert gone["ok"] and gone["data"]["who"] == "Binh" and tools["rota_delete"].execute({"doc_id": "2026-W37"})["ok"] is False
    assert tools["rota_delete"].execute({})["ok"] is False
    # another space of the same business has its own documents
    other = ToolContext(db=db, room_id=room_id + 1, sender_member_id=m[0], tool_config={"packs": [{"pack": "collections"}]})
    assert build_tools(other)["rota_find"].execute({})["documents"] == []


def test_gate1_checks_pack_ids_and_static_override_names(db):
    room_id, m, k, bid = _setup(db)
    for patch, needle in [({"tool_packs": [{"pack": "nope"}]}, "no pack 'nope'"),
                          ({"tool_packs": [{"pack": "lunch_ledger", "tools": {"zzz": {"enabled": False}}}]}, "['zzz']")]:
        d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
        k.store.update_draft(d["id"], patch, actor="admin")
        with pytest.raises(GateError) as exc:
            k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="t")
        assert any(f[0] == "schema" and needle in f[1] for f in exc.value.failures), exc.value.failures
    # collections' names depend on the space, so its overrides are not checked at publish…
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"tool_packs": [{"pack": "collections", "tools": {"rota_find": {"enabled": False}}}]}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="t")
    # …and apply at compose time
    ctx = ToolContext(db=db, room_id=room_id, tool_config={"packs": [{"pack": "collections", "tools": {"rota_find": {"enabled": False}}}]})
    assert set(build_tools(ctx)) == {"rota_upsert", "rota_delete"}
    with pytest.raises(PackError):
        build_tools(ToolContext(db=db, room_id=room_id, tool_config={"packs": [{"pack": "collections", "tools": {"zzz": {}}}]}))


async def test_a_turn_writes_a_document_and_replies_in_prose(db, monkeypatch):
    room_id, m, k, bid = _setup(db)
    _publish_with_collections(k)
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["names"] = [t["name"] for t in tool_manifest(ctx)]
        args = {"data": {"week": "2026-W36", "who": "M2", "brings": "cards"}}
        res = build_tools(ctx)["rota_upsert"].execute(args)
        return TurnResult(final_text="Đã ghi: tuần 36 M2 mang bài.", turn_id="t-rota",
                          tools=[ToolInvocation("rota_upsert", args, res)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix tuần 36 M2 mang bài nhé")
    assert seen["names"][-3:] == ["rota_find", "rota_upsert", "rota_delete"] and "propose_meal" in seen["names"]
    assert reply.kind == "bot" and reply.body == "Đã ghi: tuần 36 M2 mang bài." and reply.attachments is None
    col = k.data.get_collection(bid, "rota")
    doc = k.data.get_document(col, room_id, "2026-W36")
    assert doc["data"]["who"] == "M2" and doc["created_by"] == str(m[0])
    trace = k.store.get_trace(str(room_id), "t-rota")
    assert trace["summary"]["tools"] == ["rota_upsert"] and trace["tools"][0]["result"]["doc_id"] == "2026-W36"


async def test_a_reply_that_totals_find_rows_is_caught_and_a_quoted_value_is_not(db, monkeypatch):
    room_id, m, k, bid = _setup(db)
    _publish_with_collections(k)
    col = k.data.get_collection(bid, "rota")
    k.data.upsert_document(col, room_id, {"week": "2026-W36", "who": "An", "players": 60000}, actor="admin")
    k.data.upsert_document(col, room_id, {"week": "2026-W37", "who": "An", "players": 45000}, actor="admin")

    def engine(text):
        async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
            res = build_tools(ctx)["rota_find"].execute({"where": {"who": "An"}})
            return TurnResult(final_text=text, turn_id=f"t-{len(text)}", tools=[ToolInvocation("rota_find", {"where": {"who": "An"}}, res)])
        return fake

    monkeypatch.setattr(agent_mod, "run_turn", engine("Tổng cộng An có 105,000đ."))       # 60000 + 45000: computed by the model
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix An bao nhiêu")
    trace = k.store.get_trace(str(room_id), f"t-{len('Tổng cộng An có 105,000đ.')}")
    assert [v["plugin"] for v in trace["summary"]["verdicts"]] == ["app.validate.unbacked_amounts"]
    assert reply.kind == "bot"

    monkeypatch.setattr(agent_mod, "run_turn", engine("Tuần 36 An ghi 60,000đ."))            # a value a tool returned
    await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix An tuần 36")
    trace = k.store.get_trace(str(room_id), f"t-{len('Tuần 36 An ghi 60,000đ.')}")
    assert trace["summary"]["verdicts"] == []
