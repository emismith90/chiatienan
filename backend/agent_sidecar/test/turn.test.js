/**
 * Event normalization, turn caps, answer assembly.
 *
 * The two tests that matter most are the diacritic join and the cap semantics.
 * Both encode production incidents, and both would fail silently in a way no user
 * would report as a bug — one shreds Vietnamese, the other turns a slow-but-correct
 * reply into a ⚠️.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildPromptOptions,
  finalAnswer,
  formatError,
  isNarration,
  runTurn,
  splitAtSeams,
  stripNarration,
} from "../turn.js";

test("text fragments join with empty string, never a separator", () => {
  // Joining with a separator put a blank line between every token and shredded
  // Vietnamese diacritics into "V ẫn không được đâu Kun" in production — a string
  // still present in the recorded prod corpus (p142, p144).
  assert.equal(finalAnswer(["Không", "—", "hiện", " tại"], 0), "Không—hiện tại");
});

test("a shredded reply is what we are guarding against", () => {
  // The real p144 opening. If fragments were joined with " " this is what comes out.
  const fragments = ["V", "ẫn", " không được đâu A4"];
  assert.equal(finalAnswer(fragments, 0), "Vẫn không được đâu A4");
});

test("narration before the last tool call is dropped", () => {
  assert.equal(finalAnswer(["Mình đọc skill record-meal.", "Đã ghi bữa trưa."], 1),
    "Đã ghi bữa trưa.");
});

test("an all-narration reply is kept rather than emptied", () => {
  // A bad reply still beats an empty bubble.
  assert.notEqual(stripNarration("Mình đang kiểm tra."), "");
  assert.notEqual(stripNarration("Mình đọc skill ghi trả tiền."), "");
});

test("the narration production actually leaked is recognized", () => {
  // Every one of these is a real prod reply that reached the room (prod corpus).
  for (const line of [
    "mình đọc skill phù hợp rồi xử lý",
    "Mình sẽ tìm thành viên A2 rồi cập nhật tên thành A2.",
    "mình làm theo skill ghi trả tiền",
    "Tôi sẽ ghi khoản A6 trả A3 theo quy trình ghi trả tiền.",
    "mình xem schema bốc thăm và danh sách nhóm trước",
  ]) {
    assert.ok(isNarration(line), line);
  }
});

test("an ordinary answer is not mistaken for narration", () => {
  for (const line of [
    "Đã ghi bữa trưa nhé.",
    "A4 trả A3 61,000đ",
    "Không ai nợ ai cả.",
    "Số dư của A3: được nợ 40,000đ",
  ]) {
    assert.ok(!isNarration(line), line);
  }
});

test("seams split on newlines and sentence ends", () => {
  assert.deepEqual(splitAtSeams("Một.\nHai. Ba"), ["Một.", "Hai.", "Ba"]);
});

// --------------------------------------------------------------------------- //
// caps
// --------------------------------------------------------------------------- //

/** A session stub that emits N tool calls, then some text. */
function sessionThatCallsNTools(n, { textAfter = "Đã ghi xong." } = {}) {
  const listeners = [];
  let aborted = false;
  return {
    aborted: () => aborted,
    subscribe(listener) {
      listeners.push(listener);
      return () => {
        const at = listeners.indexOf(listener);
        if (at >= 0) listeners.splice(at, 1);
      };
    },
    abort() {
      aborted = true;
      return Promise.resolve();
    },
    getSessionStats() {
      return { tokens: { input: 100, output: 50 } };
    },
    async prompt() {
      const fire = (event) => listeners.slice().forEach((l) => l(event));
      fire({ type: "agent_start" });
      for (let i = 0; i < n; i += 1) {
        if (aborted) break;
        fire({ type: "tool_execution_start", toolCallId: `c${i}`, toolName: "find_members", args: {} });
        fire({
          type: "tool_execution_end",
          toolCallId: `c${i}`,
          toolName: "find_members",
          result: { ok: true },
          isError: false,
        });
      }
      fire({
        type: "message_update",
        assistantMessageEvent: { type: "text_delta", delta: textAfter },
      });
      fire({ type: "agent_end", messages: [], willRetry: false });
    },
  };
}

test("a max_tools breach is a PARTIAL ANSWER, not an error", async () => {
  // agent.py:374-384 — the loop cancelled and broke; result.error stayed None and
  // chat.py:558 posted the accumulated text as a normal reply. If a cap became an
  // error, every capped turn would flip to a ⚠️ message in the room.
  const session = sessionThatCallsNTools(50);
  const r = await runTurn(session, { turn_id: "t1", message: "x", max_tools: 3 }, () => {});
  assert.equal(r.error, null);
  assert.equal(r.capped, true);
  assert.equal(r.tools.length, 3);
  assert.ok(r.final_text.length > 0);
  assert.ok(session.aborted(), "a cap must actually cancel the run");
});

test("a max_seconds breach behaves the same way", async () => {
  const listeners = [];
  const session = {
    subscribe(l) { listeners.push(l); return () => {}; },
    abort() { return Promise.resolve(); },
    async prompt() {
      listeners.forEach((l) => l({ type: "agent_start" }));
      listeners.forEach((l) => l({
        type: "message_update",
        assistantMessageEvent: { type: "text_delta", delta: "Đang tính…" },
      }));
      await new Promise((resolve) => setTimeout(resolve, 80));
    },
  };
  const r = await runTurn(session, { turn_id: "t", message: "x", max_seconds: 0.01 }, () => {});
  assert.equal(r.capped, true);
  assert.equal(r.error, null);
  assert.equal(r.final_text, "Đang tính…");
});

test("an uncapped turn reports no cap and every tool", async () => {
  const r = await runTurn(sessionThatCallsNTools(2), { turn_id: "t", message: "x" }, () => {});
  assert.equal(r.capped, false);
  assert.equal(r.error, null);
  assert.equal(r.tools.length, 2);
  assert.equal(r.stats.tool_calls, 2);
  assert.equal(r.stats.tokens, 150);
});

// --------------------------------------------------------------------------- //
// events
// --------------------------------------------------------------------------- //

test("events are emitted in the app's frozen wire format", async () => {
  const seen = [];
  await runTurn(sessionThatCallsNTools(1), { turn_id: "t7", message: "x" }, (e) => seen.push(e));
  assert.deepEqual(seen.map((e) => e.type), [
    "agent.run.started",
    "agent.tool.start",
    "agent.tool.result",
    "agent.text.delta",
    "agent.run.finished",
  ]);
  // use-room.ts:50-89 reads exactly these fields
  assert.ok(seen.every((e) => e.turn_id === "t7"));
  const start = seen.find((e) => e.type === "agent.tool.start");
  assert.equal(start.call_id, "c0");
  assert.equal(start.name, "find_members");
  const result = seen.find((e) => e.type === "agent.tool.result");
  assert.equal(result.status, "completed");
});

test("a failing tool reports status error but does not fail the turn", async () => {
  const listeners = [];
  const session = {
    subscribe(l) { listeners.push(l); return () => {}; },
    abort() { return Promise.resolve(); },
    async prompt() {
      const fire = (e) => listeners.forEach((l) => l(e));
      fire({ type: "agent_start" });
      fire({ type: "tool_execution_start", toolCallId: "c", toolName: "propose_meal", args: {} });
      fire({
        type: "tool_execution_end",
        toolCallId: "c",
        toolName: "propose_meal",
        result: { ok: false, error: "Ngày nào?" },
        isError: false,
      });
      fire({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "Ngày nào?" } });
    },
  };
  const seen = [];
  const r = await runTurn(session, { turn_id: "t", message: "x" }, (e) => seen.push(e));
  // ok:false is a clarifying question, not an error (tools.py:8)
  assert.equal(r.error, null);
  assert.equal(r.tools[0].result.ok, false);
  assert.ok(seen.some((e) => e.type === "agent.run.finished"));
});

test("a thrown prompt becomes one error string and a run.error event", async () => {
  const session = {
    subscribe() { return () => {}; },
    abort() { return Promise.resolve(); },
    async prompt() { throw new Error("provider exploded"); },
  };
  const seen = [];
  const r = await runTurn(session, { turn_id: "t", message: "x" }, (e) => seen.push(e));
  assert.match(r.error, /provider exploded/);
  assert.equal(seen.at(-1).type, "agent.run.error");
});

test("a blocked model does not reach the room as the vendor's own text", () => {
  // prod corpus p271/p273 posted "Model Blocked … by your team admin settings"
  // in English as the bot's reply, twice.
  const message = formatError(new Error("Model Blocked This model has been blocked by your team admin settings."));
  assert.ok(!/team admin/i.test(message));
  assert.match(message, /Model không dùng được/);
});

test("a rate limit gets its own Vietnamese message", () => {
  assert.match(formatError(new Error("429 Too Many Requests")), /quá tải/);
});

test("images become base64 content blocks, and no images means no option", () => {
  assert.equal(buildPromptOptions({ message: "x" }), undefined);
  const options = buildPromptOptions({
    images: [{ data: "aGk=", mimeType: "image/jpeg" }],
  });
  assert.equal(options.images[0].type, "image");
  assert.equal(options.images[0].source.mediaType, "image/jpeg");
  assert.equal(options.images[0].source.data, "aGk=");
});
