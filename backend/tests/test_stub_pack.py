"""A stub pack with two tools and one draft kind runs a turn end to end (plan Task 3.4).

Nothing in the host names the stub: the kernel registers it, a published profile
enables it, the engine (faked) is offered exactly its tools, its render decision
becomes a card of its own kind through the kernel's persist stage and the host's
generic draft store, and the card commits through the same `commit_any` route the
lunch cards use — with the stub's `commit` and `card`.
"""
import app.agent as agent_mod
from app import chat, drafts
from app.agent import ToolInvocation, TurnResult
from app.hostadapters import RoomCards
from app.kernel import kernel_for
from app.tools import build_tools, tool_manifest
from kernos.kernel import Body, Draft
from kernos.packs import BasePack, DraftKind, PackTool, err
from tests.test_ledger import _seed_room

NOTES: list[str] = []


class StubPack(BasePack):
    id, version = "stub", "1"

    def tools(self, ctx):
        def echo(args, _tool_ctx=None):
            return {"ok": True, "type": "echo", "text": (args or {}).get("text", "")}

        def propose_note(args, _tool_ctx=None):
            text = (args or {}).get("text")
            if not text:
                return err("Missing text.")
            return {"ok": True, "type": "note_draft", "text": text, "space": ctx.space_id}

        schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        return {"echo": PackTool("echo", "Echo text.", schema, echo),
                "propose_note": PackTool("propose_note", "Propose a note for confirmation.", schema, propose_note)}

    def draft_kinds(self):
        def commit(session, space_id, att, *, logged_by):
            NOTES.append(f"{space_id}:{att['text']}:{logged_by}")
            return {"noted": att["text"]}

        def card(session, space_id, att, res):
            return f"Noted: {res['noted']}", {"type": "note", **res}

        return {"note_draft": DraftKind("note_draft", commit, card=card,
                                        stamps=frozenset({"raw_input", "turn_id"}))}

    def render(self, result):
        note = result.last_result("propose_note")
        if note and note.get("type") == "note_draft":
            return Draft("note_draft", {"text": note["text"]})
        echo = result.last_result("echo")
        if echo:
            return Body(f"echo: {echo['text']}", {"type": "echo"}, claimed_by_pack=True)
        return None


async def test_a_stub_pack_runs_a_turn_end_to_end(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)
    k.register_packs(StubPack())
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"tool_packs": [{"pack": "stub"}]}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["manifest"] = [t["name"] for t in tool_manifest(ctx)]
        tools = build_tools(ctx)
        res = tools["propose_note"].execute({"text": "buy milk"})
        return TurnResult(final_text="ignored prose", turn_id="t-stub",
                          tools=[ToolInvocation("propose_note", {"text": "buy milk"}, res)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix note: buy milk")

    assert seen["manifest"] == ["echo", "propose_note"]            # only the stub's tools reach the engine
    assert reply.kind == "note_draft" and reply.body == ""
    att = reply.attachments
    assert att["type"] == "note_draft" and att["status"] == "pending" and att["text"] == "buy milk"
    assert att["turn_id"] == "t-stub" and att["raw_input"] == "@phoenix note: buy milk"
    assert "logged_by" not in att                                   # not in the kind's stamps

    cards = RoomCards(db)
    assert [c.id for c in cards.pending(str(room_id))] == [reply.id]
    with db.session() as s:
        card = drafts.commit_any(s, reply.id, room_id, logged_by=str(m[0]))
        assert card.kind == "bot" and card.body == "Noted: buy milk" and card.attachments == {"type": "note", "noted": "buy milk"}
        assert s.get(type(reply), reply.id).attachments["status"] == "committed"
    assert NOTES == [f"{room_id}:buy milk:{m[0]}"]
    assert cards.pending(str(room_id)) == []


async def test_a_stub_body_is_claimed_and_persisted_as_a_bot_message(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)
    k.register_packs(StubPack())
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"tool_packs": [{"pack": "stub"}]}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        res = build_tools(ctx)["echo"].execute({"text": "hi"})
        return TurnResult(final_text="the model says 1,000,000đ", tools=[ToolInvocation("echo", {"text": "hi"}, res)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix echo hi")
    assert reply.kind == "bot" and reply.body == "echo: hi" and reply.attachments == {"type": "echo"}
