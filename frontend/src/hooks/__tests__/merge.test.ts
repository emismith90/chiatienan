import { describe, it, expect } from "vitest";
import { mergeEvent } from "../use-room";

describe("mergeEvent", () => {
  it("appends messages, dedupes by id, toggles typing", () => {
    let s = { messages: [] as any[], typing: false };
    s = mergeEvent(s, { type: "message", id: 1, body: "hi" });
    s = mergeEvent(s, { type: "message", id: 1, body: "hi" }); // dup
    s = mergeEvent(s, { type: "bot.typing" });
    expect(s.messages.map((m) => m.id)).toEqual([1]);
    expect(s.typing).toBe(true);
    s = mergeEvent(s, { type: "message", id: 2, kind: "bot", body: "pong" });
    s = mergeEvent(s, { type: "bot.done" });
    expect(s.typing).toBe(false);
    expect(s.messages.length).toBe(2);
  });

  it("strips the event type from the stored message", () => {
    const s = mergeEvent({ messages: [], typing: false }, {
      type: "message",
      id: 5,
      body: "yo",
      kind: "text",
    });
    expect(s.messages[0]).toEqual({ id: 5, body: "yo", kind: "text" });
    expect("type" in s.messages[0]).toBe(false);
  });

  it("ignores unknown / __closed__ events without mutating state", () => {
    const start = { messages: [{ id: 1 }], typing: true };
    expect(mergeEvent(start, { type: "__closed__" })).toBe(start);
    expect(mergeEvent(start, { type: "something.else" })).toBe(start);
  });
});
