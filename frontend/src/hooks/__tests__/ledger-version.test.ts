import { describe, expect, it } from "vitest";
import { mergeEvent } from "../use-room";

const base = { messages: [], typing: false, timelines: {}, activeTurn: null, hasMore: false };

describe("mergeEvent ledger:changed", () => {
  it("bumps ledgerVersion", () => {
    const s1 = mergeEvent(base as any, { type: "ledger:changed" });
    expect(s1.ledgerVersion).toBe(1);
    const s2 = mergeEvent(s1, { type: "ledger:changed" });
    expect(s2.ledgerVersion).toBe(2);
  });

  it("ignores unrelated events", () => {
    const s1 = mergeEvent(base as any, { type: "bot.typing" });
    expect(s1.ledgerVersion ?? 0).toBe(0);
  });
});
