/**
 * The tool proxy and session construction.
 *
 * `ok:false` gets first billing: getting it backwards is a corpus-wide regression
 * that no other test catches, because nothing crashes — every clarifying question
 * just becomes an apology.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { buildAgentsFiles, KEY_ENV, OPENROUTER_BASE_URL, proxyTool, resolveModel,
  toolOptionsFor } from "../session.js";

const SCHEMA = { type: "object", properties: { total: { type: "integer" } } };

test("ok:false is a content block, NOT a throw", async () => {
  // tools.py:8 — a validation failure is a clarifying question, not an error.
  // Throwing would make the model apologize instead of asking.
  const tool = proxyTool({ name: "propose_meal", description: "d", schema: SCHEMA },
    async () => ({ ok: false, error: "Ngày nào?" }));
  const r = await tool.execute("call-1", {});
  assert.equal(r.content[0].type, "text");
  assert.match(r.content[0].text, /Ngày nào\?/);
});

test("transport death does throw", async () => {
  const tool = proxyTool({ name: "x", description: "d", schema: SCHEMA },
    async () => { throw new Error("bridge died"); });
  await assert.rejects(() => tool.execute("call-2", {}), /bridge died/);
});

test("the tool result dict is preserved in details", async () => {
  const payload = { ok: true, transfers: [{ amount: 100000 }] };
  const tool = proxyTool({ name: "settle_period", description: "d", schema: SCHEMA },
    async () => payload);
  assert.deepEqual((await tool.execute("c", {})).details, payload);
});

test("the proxy passes the call id, name and args through to Python", async () => {
  const seen = [];
  const tool = proxyTool({ name: "propose_meal", description: "d", schema: SCHEMA },
    async (...args) => { seen.push(args); return { ok: true }; });
  await tool.execute("call-9", { total: 300000 });
  assert.deepEqual(seen, [["call-9", "propose_meal", { total: 300000 }]]);
});

test("a bad schema fails at build time, not at call time", () => {
  // Better to refuse to start than to tell the model about a shape the tool rejects.
  assert.throws(() => proxyTool(
    { name: "x", description: "d", schema: { type: "object", properties: { a: { minLength: 2 } } } },
    async () => ({})), /minLength/);
});

// --------------------------------------------------------------------------- //
// skills reach the model as context files
// --------------------------------------------------------------------------- //

test("a skill BODY reaches the model, as an agents file", () => {
  // pi's skillsOverride needs a filePath on disk and defers the body to `read`,
  // which noTools:"builtin" removes — so the body ships as inline content instead.
  const files = buildAgentsFiles({
    skills: [{ name: "record-meal", description: "d", body: "UNIQUE_BODY_MARKER_42" }],
    context_files: [],
  });
  assert.equal(files.length, 1);
  assert.match(files[0].content, /UNIQUE_BODY_MARKER_42/);
  assert.match(files[0].content, /record-meal/);
});

test("context files keep their own path and content", () => {
  const files = buildAgentsFiles({
    context_files: [{ path: "money-safety", content: "KHÔNG chạy python/bash để tính tiền" }],
    skills: [],
  });
  assert.match(files[0].path, /money-safety/);
  assert.match(files[0].content, /KHÔNG chạy python/);
});

test("every skill and context file is present, none dropped", () => {
  const files = buildAgentsFiles({
    context_files: [{ path: "money-safety", content: "a" }],
    skills: [
      { name: "record-meal", body: "b" },
      { name: "settle", body: "c" },
      { name: "payments", body: "d" },
      { name: "roster", body: "e" },
    ],
  });
  assert.equal(files.length, 5);
});

// --------------------------------------------------------------------------- //
// model routing
// --------------------------------------------------------------------------- //

const RUNTIME = { getModel: (id) => ({ id }) };

test("a text turn uses PI_MODEL", () => {
  assert.deepEqual(resolveModel(RUNTIME, { model: "a/text" }), { id: "a/text" });
});

test("an image turn uses the vision model", () => {
  const model = resolveModel(RUNTIME, {
    model: "a/text", vision_model: "q/vl", images: [{ data: "x" }],
  });
  assert.deepEqual(model, { id: "q/vl" });
});

test("an image turn with no vision model FAILS LOUDLY rather than dropping the photo", () => {
  // The primary is text-only, so falling back would have the model invent a total —
  // wrong money that looks right, which is worse than an error (design §12).
  assert.throws(
    () => resolveModel(RUNTIME, { model: "a/text", images: [{ data: "x" }] }),
    /images but PI_VISION_MODEL is not set/,
  );
});

test("an unavailable vision model does not silently fall back to text", () => {
  assert.throws(
    () => resolveModel({ getModel: () => null, getModels: () => [] },
      { model: "a/text", vision_model: "q/gone", images: [{ data: "x" }] }),
    /vision turn — not falling back/,
  );
});

test("a missing PI_MODEL is an error, not a default", () => {
  assert.throws(() => resolveModel(RUNTIME, {}), /PI_MODEL is not set/);
});

test("the base URL is a constant and the key env name is the real one", () => {
  assert.equal(OPENROUTER_BASE_URL, "https://openrouter.ai/api/v1");
  assert.equal(KEY_ENV, "OPEN_ROUTER_KEY");
});

// --------------------------------------------------------------------------- //
// builtin tools
// --------------------------------------------------------------------------- //

test("no builtin tools means money-safety is structural, not advisory", () => {
  // Without `bash` the model cannot compute money, so money-safety.mdc's request
  // becomes a property of the harness rather than a plea in a prompt.
  assert.deepEqual(toolOptionsFor([], ["propose_meal"]), { noTools: "builtin" });
  assert.deepEqual(toolOptionsFor(undefined, ["propose_meal"]), { noTools: "builtin" });
});

test("builtin tools are allowlisted ALONGSIDE the money tools, never instead", () => {
  // pi treats `tools` as an allowlist, so omitting our own names would silently
  // disable them all and leave the model holding only bash — the worst of both worlds.
  const options = toolOptionsFor(["read", "write", "bash"],
    ["propose_meal", "settle_period"]);
  assert.deepEqual(options.tools,
    ["read", "write", "bash", "propose_meal", "settle_period"]);
  assert.equal(options.noTools, undefined);
});
