import { describe, it, expect } from "vitest";
import { parseSSE } from "../sse";

describe("parseSSE", () => {
  it("parses complete events and keeps the partial remainder", () => {
    const { events, rest } = parseSSE(
      'data: {"type":"message","id":1}\n\ndata: {"type":"bot.typing"}\n\ndata: {"type":"mess'
    );
    expect(events).toEqual([{ type: "message", id: 1 }, { type: "bot.typing" }]);
    expect(rest).toBe('data: {"type":"mess');
  });
});
