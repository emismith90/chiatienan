"""The generated collection tools end to end (plan Task 5.2)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import app.agent as agent_mod
from app import chat
from app.agent import ToolInvocation, TurnResult
from app import drafts
from app.kernel import kernel_for
from app.packs import LEGACY_ORDER
from app.tools import ToolContext, build_tools, tool_manifest
from kernos.content.errors import GateError
from kernos.packs import PackError
from tests.test_ledger import _seed_room

ROTA = {"type": "object", "required": ["week", "who"],
        "properties": {"week": {"type": "string", "description": "ISO week"}, "who": {"type": "string"},
                       "brings": {"type": "string", "enum": ["cards", "chips"]}, "players": {"type": "integer"}}}
SIDECAR = Path(__file__).resolve().parent.parent / "agent_sidecar"


def _require_sidecar_deps() -> None:
    """The one place a pytest test executes the sidecar's own JavaScript.

    That is deliberate — a Python/TypeBox schema drift does not show up as a red build,
    it shows up as the model sending arguments the tool rejects — so the check must not
    quietly disappear. Hence the asymmetry: a contributor without `npm ci` gets a skip
    telling them the command, but in CI a missing install is a **failure**, because a
    silent skip there would lose exactly the signal this test exists for.
    """
    if (SIDECAR / "node_modules").is_dir():
        return
    message = (f"the sidecar's dependencies are not installed: run `npm ci` in {SIDECAR}. "
               "CI installs them in the backend job (.github/workflows/ci.yml).")
    if os.environ.get("CI"):
        raise AssertionError(f"CI must install the sidecar before pytest — {message}")
    pytest.skip(message)


ROTA_TOOLS = ["rota_find", "rota_create", "rota_update", "rota_delete", "rota_search"]


def _setup(db, n=2, options=None):
    room_id, m = _seed_room(db, n)
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    k.data.put_collection(bid, "rota", name="Card rota", description="who brings what", schema=ROTA,
                          key="week", indexed=["who"], actor="admin", reserved=k.reserved_tool_names(),
                          options=options)
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
    n = len(LEGACY_ORDER)
    assert names[:n] == [t["name"] for t in tool_manifest()] and names[n:] == ROTA_TOOLS
    tools = build_tools(ctx)
    assert "who brings what" in tools["rota_find"].description and "never a count" in tools["rota_find"].description
    assert tools["rota_create"].input_schema["properties"]["data"] == {
        "type": "object", "properties": ROTA["properties"], "required": ["week", "who"]}
    assert "week" not in tools["rota_update"].input_schema["properties"]["changes"]["properties"]
    assert "confirms it on a card" in tools["rota_create"].description
    assert tools["rota_find"].input_schema["properties"]["where"]["properties"] == {"who": {"type": "string"}}
    # the sidecar's own converter accepts the whole manifest
    _require_sidecar_deps()
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


def test_writes_propose_and_change_nothing_until_confirmed(db):
    room_id, m, k, bid = _setup(db)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0], tool_config={"packs": [{"pack": "collections"}]})
    tools = build_tools(ctx)
    col = k.data.get_collection(bid, "rota")
    bad = tools["rota_create"].execute({"data": {"week": "2026-W36", "who": "An", "brings": "beer"}})
    assert bad["ok"] is False and "brings" in bad["error"]
    assert tools["rota_create"].execute({"data": "nope"})["ok"] is False
    assert tools["rota_create"].execute({"data": {"who": "An"}})["ok"] is False          # no key
    prop = tools["rota_create"].execute({"data": {"week": "2026-W36", "who": "An", "brings": "cards"}})
    assert prop["ok"] and prop["type"] == "record_action" and "NOT saved" in prop["note"]
    a = prop["action"]
    assert (a["op"], a["collection"], a["before"], a["after"]["who"]) == ("create", "rota", None, "An")
    assert k.data.find_documents(col, room_id)["documents"] == []                        # nothing written
    k.data.upsert_document(col, room_id, {"week": "2026-W37", "who": "Binh", "players": 4}, actor="admin")
    up = tools["rota_update"].execute({"doc_id": "2026-W37", "changes": {"brings": "chips"}})
    assert up["ok"] and up["action"]["after"] == {"week": "2026-W37", "who": "Binh", "players": 4, "brings": "chips"}
    assert tools["rota_update"].execute({"doc_id": "2026-W37", "changes": {"who": "Binh"}})["ok"] is False   # no change
    assert tools["rota_update"].execute({"doc_id": "2026-W37", "changes": {"week": "X"}})["ok"] is False     # the key
    assert tools["rota_update"].execute({"doc_id": "nope", "changes": {"who": "A"}})["ok"] is False
    gone = tools["rota_delete"].execute({"doc_id": "2026-W37"})
    assert gone["ok"] and gone["action"]["op"] == "delete" and gone["action"]["after"] is None
    assert tools["rota_delete"].execute({})["ok"] is False
    assert k.data.get_document(col, room_id, "2026-W37")["data"]["who"] == "Binh"       # still there
    found = tools["rota_find"].execute({"where": {"who": "Binh"}})
    assert found["ok"] and [d["doc_id"] for d in found["documents"]] == ["2026-W37"] and found["more"] is False
    assert tools["rota_find"].execute({"where": {"players": 6}})["ok"] is False
    assert tools["rota_find"].execute({"where": "x"})["ok"] is False
    # another space of the same business has its own documents
    other = ToolContext(db=db, room_id=room_id + 1, sender_member_id=m[0], tool_config={"packs": [{"pack": "collections"}]})
    assert build_tools(other)["rota_find"].execute({})["documents"] == []


def test_confirm_false_writes_at_once(db):
    room_id, m, k, bid = _setup(db, options={"confirm": False})
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=m[0],
                                    tool_config={"packs": [{"pack": "collections"}]}))
    assert "Writes immediately" in tools["rota_create"].description
    ok = tools["rota_create"].execute({"data": {"week": "2026-W36", "who": "An", "players": 6}})
    assert ok == {"ok": True, "type": "rota_saved", "collection": "rota", "op": "create", "doc_id": "2026-W36", "label": "2026-W36"}
    col = k.data.get_collection(bid, "rota")
    assert k.data.get_document(col, room_id, "2026-W36")["created_by"] == str(m[0])
    assert tools["rota_update"].execute({"doc_id": "2026-W36", "changes": {"players": 7}})["ok"]
    assert k.data.get_document(col, room_id, "2026-W36")["data"]["players"] == 7
    assert tools["rota_delete"].execute({"doc_id": "2026-W36"})["ok"]
    assert k.data.get_document(col, room_id, "2026-W36") is None


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
    assert set(build_tools(ctx)) == {"rota_create", "rota_update", "rota_delete", "rota_search"}
    with pytest.raises(PackError):
        build_tools(ToolContext(db=db, room_id=room_id, tool_config={"packs": [{"pack": "collections", "tools": {"zzz": {}}}]}))


async def test_a_turn_ends_on_a_card_and_confirming_it_writes(db, monkeypatch):
    room_id, m, k, bid = _setup(db)
    _publish_with_collections(k)
    col = k.data.get_collection(bid, "rota")
    k.data.upsert_document(col, room_id, {"week": "2026-W35", "who": "An"}, actor="admin")
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["names"] = [t["name"] for t in tool_manifest(ctx)]
        tools = build_tools(ctx)
        a1 = {"data": {"week": "2026-W36", "who": "M2", "brings": "cards"}}
        a2 = {"doc_id": "2026-W35", "changes": {"brings": "chips"}}
        r1, r2 = tools["rota_create"].execute(a1), tools["rota_update"].execute(a2)
        return TurnResult(final_text="Mình đề xuất ghi tuần 36 — bấm Xác nhận nhé.", turn_id="t-rota",
                          tools=[ToolInvocation("rota_create", a1, r1), ToolInvocation("rota_update", a2, r2)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    card = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix tuần 36 M2 mang bài nhé")
    assert seen["names"][-5:] == ROTA_TOOLS and "propose_meal" in seen["names"]
    assert card.kind == "record_draft" and card.attachments["status"] == "pending"
    assert [a["op"] for a in card.attachments["actions"]] == ["create", "update"]
    assert "Confirm" in card.body and "brings: — → chips" in card.body
    assert k.data.get_document(col, room_id, "2026-W36") is None                          # not yet
    with db.session() as s:
        drafts.commit_any(s, card.id, room_id, logged_by=str(m[1]))
    assert k.data.get_document(col, room_id, "2026-W36")["data"]["who"] == "M2"
    assert k.data.get_document(col, room_id, "2026-W35")["data"] == {"week": "2026-W35", "who": "An", "brings": "chips"}
    trace = k.store.get_trace(str(room_id), "t-rota")
    assert trace["summary"]["tools"] == ["rota_create", "rota_update"]


async def test_a_card_whose_record_changed_since_is_refused_whole(db, monkeypatch):
    room_id, m, k, bid = _setup(db)
    _publish_with_collections(k)
    col = k.data.get_collection(bid, "rota")
    k.data.upsert_document(col, room_id, {"week": "2026-W35", "who": "An"}, actor="admin")

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        tools = build_tools(ctx)
        a1, a2 = {"data": {"week": "2026-W36", "who": "M2"}}, {"doc_id": "2026-W35", "changes": {"who": "Chi"}}
        return TurnResult(final_text="ok", turn_id="t-x", tools=[
            ToolInvocation("rota_create", a1, tools["rota_create"].execute(a1)),
            ToolInvocation("rota_update", a2, tools["rota_update"].execute(a2))])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    card = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix …")
    k.data.upsert_document(col, room_id, {"week": "2026-W35", "who": "Binh"}, actor="admin")   # someone else, meanwhile
    with pytest.raises(Exception, match="changed since"):
        with db.session() as s:
            drafts.commit_any(s, card.id, room_id, logged_by=str(m[1]))
    assert k.data.get_document(col, room_id, "2026-W36") is None                           # all or nothing
    with db.session() as s:
        assert s.get(type(card), card.id).attachments["status"] == "pending"


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


def test_a_journal_gets_find_append_search_and_they_convert_in_the_sidecar(db):
    room_id, m, k, bid = _setup(db)
    log = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "who": {"type": "string"}}}
    k.data.put_collection(bid, "log", name="Room log", schema=log, key="", mode="journal", actor="admin",
                          reserved=k.reserved_tool_names())
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0], tool_config={"packs": [{"pack": "collections"}]})
    tools = build_tools(ctx)
    assert [n for n in tools if n.startswith("log_")] == ["log_find", "log_append", "log_search"]
    assert "permanent" in tools["log_append"].description
    prop = tools["log_append"].execute({"data": {"text": "An đặt cọc 500k"}})
    assert prop["ok"] and prop["type"] == "record_action" and prop["action"]["op"] == "append"
    col = k.data.get_collection(bid, "log")
    assert k.data.find_documents(col, room_id)["documents"] == []          # proposed, not written
    from kernos.data import actions
    with db.session() as s:
        assert actions.apply(k.data, prop["action"], actor="member:1", session=s)["doc_id"] == "000001"
    fix = tools["log_append"].execute({"data": {"text": "Chi đặt cọc, không phải An"}, "corrects": "000001"})
    assert fix["ok"] and fix["action"]["corrects"] == "000001"
    with db.session() as s:
        actions.apply(k.data, fix["action"], actor="member:1", session=s)
    assert tools["log_append"].execute({"data": {"text": "x"}, "corrects": "000009"})["ok"] is False
    found = tools["log_search"].execute({"query": "dat coc"})
    assert found["ok"] and found["semantic"] == "off" and {d["doc_id"] for d in found["documents"]} == {"000001", "000002"}
    assert tools["log_search"].execute({})["ok"] is False
    assert [d["doc_id"] for d in tools["log_find"].execute({})["documents"]] == ["000002", "000001"]
    _require_sidecar_deps()
    script = ('import { toTypeBoxManifest } from "./schema.js"; let s=""; process.stdin.on("data", d => s += d);'
              'process.stdin.on("end", () => { const out = toTypeBoxManifest(JSON.parse(s)); console.log(Object.keys(out).length); });')
    manifest = {t["name"]: t["schema"] for t in tool_manifest(ctx)}
    run = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(manifest),
                         capture_output=True, text=True, cwd=SIDECAR, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == str(len(manifest))
