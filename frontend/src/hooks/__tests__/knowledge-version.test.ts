import { describe, expect, it } from "vitest";
import { mergeEvent } from "../use-room";

const base = { messages: [], typing: false, timelines: {}, activeTurn: null, hasMore: false };

describe("mergeEvent knowledge:changed", () => {
  it("bumps knowledgeVersion", () => {
    const s1 = mergeEvent(base as any, { type: "knowledge:changed" });
    expect(s1.knowledgeVersion).toBe(1);
    const s2 = mergeEvent(s1, { type: "knowledge:changed" });
    expect(s2.knowledgeVersion).toBe(2);
  });

  it("keeps the two versions independent", () => {
    // A place edit must not make the money panel refetch, and a committed meal
    // must not make the knowledge panel refetch.
    const s1 = mergeEvent(base as any, { type: "knowledge:changed" });
    expect(s1.ledgerVersion ?? 0).toBe(0);
    const s2 = mergeEvent(s1, { type: "ledger:changed" });
    expect(s2.knowledgeVersion).toBe(1);
    expect(s2.ledgerVersion).toBe(1);
  });
});
