/**
 * The JSON Schema → TypeBox converter.
 *
 * Two of these tests exist because the design names them as the cases a naive
 * converter gets silently wrong: a string enum built with `Type.Union` breaks
 * Google's API, and `{"type": ["string","integer"]}` is the one real union in all
 * 14 schemas. The rest hold the line that an unsupported keyword throws instead of
 * being dropped — a dropped constraint means the model sends arguments the tool
 * rejects, which looks like a clarifying question, not a bug.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Check } from "typebox/value";

import { SUPPORTED_KEYWORDS, toTypeBox, toTypeBoxManifest } from "../schema.js";

const FIXTURE = JSON.parse(
  readFileSync(new URL("./fixtures/tool-schemas.json", import.meta.url), "utf8"),
);

test("string enum uses StringEnum, not Type.Union", () => {
  const s = toTypeBox({
    type: "object",
    properties: { keyword: { type: "string", enum: ["since_last", "this_week"] } },
  });
  // Type.Union/Type.Literal breaks Google's API — pi docs are explicit
  assert.deepEqual(s.properties.keyword.enum, ["since_last", "this_week"]);
  assert.equal(s.properties.keyword.anyOf, undefined);
  assert.equal(s.properties.keyword.type, "string");
});

test("union type target accepts both string and integer", () => {
  // tools.py:193 and :216 — {"type": ["string","integer"]}
  const s = toTypeBox({
    type: "object",
    properties: { target: { type: ["string", "integer"] } },
    required: ["target"],
  });
  assert.ok(Check(s, { target: "linh" }));
  assert.ok(Check(s, { target: 7 }));
  assert.ok(!Check(s, { target: true }));
});

test("non-required properties are Optional", () => {
  const s = toTypeBox({
    type: "object",
    properties: { a: { type: "string" }, b: { type: "string" } },
    required: ["a"],
  });
  assert.ok(Check(s, { a: "x" }));
  assert.ok(!Check(s, { b: "x" }));
});

test("descriptions survive, because they are the model's instructions", () => {
  const s = toTypeBox({
    type: "object",
    properties: { total: { type: "integer", description: "Bill total, integer VND." } },
  });
  assert.match(s.properties.total.description, /integer VND/);
});

test("every one of the 14 real schemas converts and validates", () => {
  assert.equal(Object.keys(FIXTURE).length, 14);
  for (const [name, schema] of Object.entries(FIXTURE)) {
    assert.doesNotThrow(() => toTypeBox(schema, name), name);
  }
  assert.equal(Object.keys(toTypeBoxManifest(FIXTURE)).length, 14);
});

test("the six real enums keep their values", () => {
  const converted = toTypeBoxManifest(FIXTURE);
  assert.deepEqual(converted.propose_meal.properties.discount_split.enum,
    ["proportional", "equal"]);
  assert.equal(converted.settle_period.properties.keyword.enum.length, 7);
  assert.deepEqual(converted.propose_payment.properties.mode.enum, ["gross", "offset"]);
  // `keyword` is shared by reference across four tools (tools.py:841, :847)
  for (const tool of ["resolve_period", "settle_period", "member_statement",
                      "get_period_summary"]) {
    assert.equal(converted[tool].properties.keyword.enum.length, 7, tool);
  }
});

test("an unsupported keyword throws rather than being dropped", () => {
  assert.throws(
    () => toTypeBox({ type: "object", properties: { a: { type: "string", minLength: 3 } } }),
    /minLength/,
  );
});

test("an array with no items throws", () => {
  assert.throws(() => toTypeBox({ type: "array" }), /items/);
});

test("an unknown type throws", () => {
  assert.throws(() => toTypeBox({ type: "date" }), /unsupported type/);
});

test("a non-string enum throws rather than guessing", () => {
  assert.throws(() => toTypeBox({ type: "integer", enum: [1, 2] }), /only supported on strings/);
});

test("SUPPORTED_KEYWORDS is exactly the six the schemas use", () => {
  assert.deepEqual([...SUPPORTED_KEYWORDS].sort(),
    ["description", "enum", "items", "properties", "required", "type"]);
});

test("a realistic propose_meal payload validates end to end", () => {
  const s = toTypeBox(FIXTURE.propose_meal, "propose_meal");
  // the shape ~deepseek/deepseek-v4-flash-latest actually returned when probed
  assert.ok(Check(s, {
    payer: 1,
    participants: [1, 2, 3],
    total: 154000,
    items: [
      { member: 1, amount: 45000, label: "cơm tấm" },
      { member: 2, amount: 55000, label: "matcha latte" },
      { member: 3, amount: 39000 },
    ],
    adjustments: [{ member: 2, amount: 9602 }],
    discount_split: "proportional",
    guests: ["Emi"],
    dish: "trà sữa",
  }));
});

test("propose_meal rejects the money mistakes that matter", () => {
  const s = toTypeBox(FIXTURE.propose_meal, "propose_meal");
  assert.ok(!Check(s, { participants: [1], total: "154000" }), "string total");
  assert.ok(!Check(s, { participants: ["An"], total: 1 }), "names instead of ids");
  assert.ok(!Check(s, { participants: [1], total: 1, discount_split: "prorata" }), "bad enum");
  assert.ok(!Check(s, { participants: [1], total: 1, items: [{ member: 1 }] }), "item with no amount");
  assert.ok(!Check(s, { total: 1 }), "participants is required");
});

test("settle_period accepts an explicit range and rejects a bad keyword", () => {
  const s = toTypeBox(FIXTURE.settle_period, "settle_period");
  assert.ok(Check(s, { keyword: "explicit", from: "2026-07-20", to: "2026-07-26" }));
  assert.ok(Check(s, {}));                       // every property is optional
  assert.ok(!Check(s, { keyword: "last_year" }));
});
