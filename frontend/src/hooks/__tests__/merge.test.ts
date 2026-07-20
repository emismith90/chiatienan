import { describe, it, expect } from "vitest";
import { mergeEvent } from "../use-room";
import type { RoomState } from "../use-room";

describe("mergeEvent", () => {
  it("appends messages, dedupes by id, toggles typing", () => {
    let s = { messages: [] as any[], typing: false, timelines: {}, activeTurn: null as string | null };
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
    const s = mergeEvent({ messages: [], typing: false, timelines: {}, activeTurn: null }, {
      type: "message",
      id: 5,
      body: "yo",
      kind: "text",
    });
    expect(s.messages[0]).toEqual({ id: 5, body: "yo", kind: "text" });
    expect("type" in s.messages[0]).toBe(false);
  });

  it("ignores unknown / __closed__ events without mutating state", () => {
    const start = { messages: [{ id: 1 }], typing: true, timelines: {}, activeTurn: null };
    expect(mergeEvent(start, { type: "__closed__" })).toBe(start);
    expect(mergeEvent(start, { type: "something.else" })).toBe(start);
  });

  it("reconciles an optimistic pending bubble with the real message", () => {
    const s0: RoomState = {
      messages: [{ id: -1, kind: "text", body: "hi", author: { id: 7 }, pending: true }],
      typing: false, timelines: {}, activeTurn: null,
    };
    const s1 = mergeEvent(s0, { type: "message", id: 42, kind: "text", body: "hi", author: { id: 7 } });
    expect(s1.messages.filter((m) => m.pending).length).toBe(0);
    expect(s1.messages.some((m) => m.id === 42)).toBe(true);
    expect(s1.messages.length).toBe(1);
  });

  it("reconciles a pending bubble sent before memberId loaded (author.id null) against the real message", () => {
    const s0: RoomState = {
      messages: [{ id: -1, kind: "text", body: "hi", author: { id: null }, pending: true }],
      typing: false, timelines: {}, activeTurn: null,
    };
    const s1 = mergeEvent(s0, { type: "message", id: 42, kind: "text", body: "hi", author: { id: 7 } });
    expect(s1.messages.filter((m) => m.pending).length).toBe(0);
    expect(s1.messages.length).toBe(1);
  });

  it("replaces an expense_draft message in place when its status changes (commit)", () => {
    const s0: RoomState = {
      messages: [
        {
          id: 42,
          kind: "expense_draft",
          body: "",
          attachments: { status: "pending", bill_total: 400_000 },
        },
      ],
      typing: false,
      timelines: {},
      activeTurn: null,
    };
    const s1 = mergeEvent(s0, {
      type: "message",
      id: 42,
      kind: "expense_draft",
      body: "",
      attachments: { status: "committed", bill_total: 400_000 },
    });
    expect(s1.messages.length).toBe(1);
    expect(s1.messages[0].attachments.status).toBe("committed");
  });
});
