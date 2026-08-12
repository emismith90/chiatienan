/**
 * The JSONL protocol, driven with a stubbed session so no API key is needed.
 *
 * This is the cheapest possible test of the protocol and it exists before
 * `session.js` is ever pointed at a real provider: framing, `req_id`
 * demultiplexing, the blocking tool round-trip, and the rule that one bad line
 * does not kill the process.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createDispatcher, createLineReader } from "../main.js";

function harness({ runTurnFn, buildSessionFn } = {}) {
  const out = [];
  const dispatcher = createDispatcher({
    write: (m) => out.push(m),
    buildSessionFn: buildSessionFn || (async () => ({ session: {}, dispose() {} })),
    runTurnFn: runTurnFn || (async () => ({
      final_text: "Đã ghi.", tools: [], error: null, capped: false, stats: null,
    })),
  });
  return { out, dispatcher };
}

test("ping answers pong with the same req_id", async () => {
  const { out, dispatcher } = harness();
  await dispatcher.handle({ type: "ping", req_id: "r1" });
  assert.deepEqual(out, [{ type: "pong", req_id: "r1", elapsed_s: 0, text: "pong" }]);
});

test("summarize returns the text the model produced", async () => {
  const { out, dispatcher } = harness({
    runTurnFn: async (_s, req) => ({ final_text: `tóm tắt: ${req.message}`, tools: [], error: null }),
  });
  await dispatcher.handle({ type: "summarize", req_id: "r2", text: "lịch sử phòng" });
  assert.deepEqual(out, [{ type: "summarize_done", req_id: "r2", text: "tóm tắt: lịch sử phòng" }]);
});

test("a failed summarize returns empty text rather than crashing a turn", async () => {
  const { out, dispatcher } = harness({
    buildSessionFn: async () => { throw new Error("no model"); },
  });
  await dispatcher.handle({ type: "summarize", req_id: "r3", text: "x" });
  assert.equal(out[0].type, "summarize_done");
  assert.equal(out[0].text, "");
});

test("the summarize session gets no tools, no skills and no system prompt", async () => {
  // summarize.py:51-56 — inheriting the run session's construction would change
  // every room's long-term memory flavor with no test catching it.
  let seen = null;
  const { dispatcher } = harness({
    buildSessionFn: async (req) => { seen = req; return { session: {}, dispose() {} }; },
  });
  await dispatcher.handle({
    type: "summarize", req_id: "r", text: "x",
    tools: [{ name: "propose_meal" }], skills: [{ name: "s" }],
    context_files: [{ path: "p", content: "c" }], system: "SYS",
  });
  assert.deepEqual(seen.tools, []);
  assert.deepEqual(seen.skills, []);
  assert.deepEqual(seen.context_files, []);
  assert.equal(seen.system, "");
});

test("a run blocks on tool_call and only finishes after tool_result", async () => {
  const { out, dispatcher } = harness({
    runTurnFn: async (_s, req, emit) => {
      emit({ type: "agent.tool.start", turn_id: req.turn_id, call_id: "c1", name: "propose_meal", args: {} });
      const result = await req.__callTool("c1", "propose_meal", { total: 300000 });
      emit({ type: "agent.tool.result", turn_id: req.turn_id, call_id: "c1",
             name: "propose_meal", status: "completed", result });
      return { final_text: "Đã ghi.", tools: [], error: null, capped: false, stats: null };
    },
    buildSessionFn: async (req, { callTool }) => {
      req.__callTool = callTool;         // expose the bridge to the stub turn
      return { session: {}, dispose() {} };
    },
  });

  const running = dispatcher.handle({ type: "run", req_id: "r9", turn_id: "t9", message: "x" });
  // Give the run a tick to reach the tool call.
  await new Promise((resolve) => setImmediate(resolve));
  const toolCall = out.find((m) => m.type === "tool_call");
  assert.ok(toolCall, "the sidecar must ask Python to run the tool");
  assert.equal(toolCall.req_id, "r9");
  assert.equal(toolCall.name, "propose_meal");
  assert.ok(!out.some((m) => m.type === "turn_done"), "the turn must still be blocked");

  await dispatcher.handle({
    type: "tool_result", req_id: "r9", call_id: "c1",
    content: JSON.stringify({ ok: true, type: "expense_draft" }),
  });
  await running;

  assert.deepEqual(out.map((m) => m.type), [
    "agent.tool.start", "tool_call", "agent.tool.result", "turn_done",
  ]);
  const done = out.at(-1);
  assert.equal(done.req_id, "r9");
  assert.equal(done.turn_id, "t9");
  assert.equal(out.find((m) => m.type === "agent.tool.result").result.ok, true);
});

test("a ping mid-turn is answered without disturbing the run", async () => {
  const { out, dispatcher } = harness({
    runTurnFn: async (_s, req) => {
      await req.__callTool("cX", "settle_period", {});
      return { final_text: "ok", tools: [], error: null, capped: false, stats: null };
    },
    buildSessionFn: async (req, { callTool }) => {
      req.__callTool = callTool;
      return { session: {}, dispose() {} };
    },
  });

  const running = dispatcher.handle({ type: "run", req_id: "run-1", turn_id: "t", message: "x" });
  await new Promise((resolve) => setImmediate(resolve));
  await dispatcher.handle({ type: "ping", req_id: "smoke-1" });

  const pong = out.find((m) => m.type === "pong");
  assert.equal(pong.req_id, "smoke-1", "each reply carries its own req_id");
  assert.ok(!out.some((m) => m.type === "turn_done"));

  await dispatcher.handle({ type: "tool_result", req_id: "run-1", call_id: "cX", content: "{}" });
  await running;
  assert.equal(out.at(-1).type, "turn_done");
  assert.equal(out.at(-1).req_id, "run-1");
});

test("a setup failure still closes the turn", async () => {
  // Otherwise chat.py waits forever and the room sees nothing at all.
  const { out, dispatcher } = harness({
    buildSessionFn: async () => { throw new Error("PI_MODEL is not set"); },
  });
  await dispatcher.handle({ type: "run", req_id: "r", turn_id: "t", message: "x" });
  assert.deepEqual(out.map((m) => m.type), ["agent.run.error", "turn_done"]);
  assert.match(out[1].error, /PI_MODEL is not set/);
  assert.equal(out[1].final_text, "");
});

test("a stray tool_result is reported, not swallowed", async () => {
  const { out, dispatcher } = harness();
  await dispatcher.handle({ type: "tool_result", req_id: "r", call_id: "ghost", content: "{}" });
  assert.equal(out[0].type, "fatal");
  assert.match(out[0].message, /ghost/);
});

test("an unknown command is fatal for that req_id only", async () => {
  const { out, dispatcher } = harness();
  await dispatcher.handle({ type: "explode", req_id: "r" });
  assert.deepEqual(out, [{ type: "fatal", req_id: "r", message: "unknown command explode" }]);
});

// --------------------------------------------------------------------------- //
// framing
// --------------------------------------------------------------------------- //

test("a line split across two chunks is handled", () => {
  const lines = [];
  const feed = createLineReader((c) => lines.push(c), () => {});
  feed('{"type":"pi');
  feed('ng","req_id":"r1"}\n');
  assert.deepEqual(lines, [{ type: "ping", req_id: "r1" }]);
});

test("two commands in one chunk both arrive", () => {
  const lines = [];
  const feed = createLineReader((c) => lines.push(c), () => {});
  feed('{"type":"ping","req_id":"a"}\n{"type":"ping","req_id":"b"}\n');
  assert.deepEqual(lines.map((c) => c.req_id), ["a", "b"]);
});

test("a CRLF line ending is tolerated", () => {
  const lines = [];
  const feed = createLineReader((c) => lines.push(c), () => {});
  feed('{"type":"ping","req_id":"r"}\r\n');
  assert.deepEqual(lines, [{ type: "ping", req_id: "r" }]);
});

test("a multi-byte Vietnamese payload survives a chunk boundary mid-character", () => {
  // Why bytes and not a Unicode line reader: pi's docs/rpc.md warns generic line
  // readers are incompatible, and Vietnamese is where that shows up.
  const payload = Buffer.from(JSON.stringify({ type: "summarize", req_id: "r", text: "Đã ghi bữa trưa" }) + "\n");
  const lines = [];
  const feed = createLineReader((c) => lines.push(c), () => {});
  const cut = 20;
  feed(payload.subarray(0, cut));
  feed(payload.subarray(cut));
  assert.equal(lines[0].text, "Đã ghi bữa trưa");
});

test("a malformed line is reported and does not stop later commands", () => {
  const lines = [];
  const bad = [];
  const feed = createLineReader((c) => lines.push(c), (t, e) => bad.push([t, e.message]));
  feed('{not json\n{"type":"ping","req_id":"after"}\n');
  assert.equal(bad.length, 1);
  assert.deepEqual(lines.map((c) => c.req_id), ["after"]);
});

test("blank lines are ignored", () => {
  const lines = [];
  const feed = createLineReader((c) => lines.push(c), () => { throw new Error("must not fire"); });
  feed('\n\n{"type":"ping","req_id":"r"}\n\n');
  assert.equal(lines.length, 1);
});
