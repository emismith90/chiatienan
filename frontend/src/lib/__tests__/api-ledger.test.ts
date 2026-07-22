import { describe, expect, it, vi, afterEach } from "vitest";
import { getLedger } from "../api";

afterEach(() => vi.restoreAllMocks());

describe("getLedger", () => {
  it("GETs the room ledger with the period query", async () => {
    const body = { period: { from: null, to: "2026-07-22", keyword: "since_last" }, balances: [], timeline: [] };
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }),
    );
    const data = await getLedger(3);
    expect(spy).toHaveBeenCalledWith("/api/rooms/3/ledger?period=since_last", expect.anything());
    expect(data.period.keyword).toBe("since_last");
  });
});
