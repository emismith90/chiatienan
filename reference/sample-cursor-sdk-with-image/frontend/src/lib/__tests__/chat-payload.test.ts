import { describe, it, expect, beforeEach } from "vitest";
import { buildAllMessages } from "@/lib/chat-payload";
import type { ChatMessage } from "@/types/chat";

// Deterministic uuid stub — reset before each test.
let n = 0;
const uuid = () => `uuid-${++n}`;

beforeEach(() => {
  n = 0;
});

describe("buildAllMessages", () => {
  // ---------------------------------------------------------------------------
  // T1.1 — Fragmented assistant bubbles coalesce
  // ---------------------------------------------------------------------------
  it("T1.1 — coalesces fragmented assistant bubbles with tool calls", () => {
    const input: ChatMessage[] = [
      { id: "u1", role: "user", content: "ask" },
      {
        id: "a1",
        role: "assistant",
        content: "",
        runId: "R1",
        toolCalls: [
          {
            id: "a",
            name: "run_sql_query",
            args: '{"sql":"SELECT..."}',
            result: '{"row_count":1}',
            status: "completed",
            startedAt: 0,
            endedAt: 1,
          },
        ],
      },
      { id: "a2", role: "assistant", content: "I found Alice", runId: "R1" },
      { id: "u2", role: "user", content: "did she..." },
    ];

    const out = buildAllMessages(input, uuid);

    expect(out).toHaveLength(4);

    // 1. User message
    expect(out[0]).toEqual({ id: "u1", role: "user", content: "ask" });

    // 2. Coalesced assistant message
    expect(out[1]).toMatchObject({
      id: "a1",
      role: "assistant",
      content: "I found Alice",
      tool_calls: [
        {
          id: "a",
          type: "function",
          function: { name: "run_sql_query", arguments: '{"sql":"SELECT..."}' },
        },
      ],
    });

    // 3. Trailing tool message
    expect(out[2]).toEqual({
      id: "uuid-1",
      role: "tool",
      tool_call_id: "a",
      content: '{"row_count":1}',
    });

    // 4. Second user message
    expect(out[3]).toEqual({ id: "u2", role: "user", content: "did she..." });
  });

  // ---------------------------------------------------------------------------
  // T1.2 — Null-result tool calls are dropped
  // ---------------------------------------------------------------------------
  it("T1.2 — drops null-result tool calls from assistant.tool_calls and tool messages", () => {
    const input: ChatMessage[] = [
      { id: "u1", role: "user", content: "query" },
      {
        id: "a1",
        role: "assistant",
        content: "results",
        toolCalls: [
          {
            id: "a",
            name: "x",
            args: "{}",
            result: null,
            status: "completed",
            startedAt: 0,
            endedAt: 1,
          },
          {
            id: "b",
            name: "y",
            args: "{}",
            result: "ok",
            status: "completed",
            startedAt: 0,
            endedAt: 1,
          },
        ],
      },
    ];

    const out = buildAllMessages(input, uuid);

    // user + assistant + tool(b) = 3 messages
    expect(out).toHaveLength(3);

    // Assistant has only one tool_call (the one with non-null result)
    const assistant = out[1];
    expect(assistant.role).toBe("assistant");
    if (assistant.role === "assistant") {
      expect(assistant.tool_calls).toHaveLength(1);
      expect(assistant.tool_calls![0].id).toBe("b");
    }

    // Only one trailing tool message for "b"
    const toolMsgs = out.filter((m) => m.role === "tool");
    expect(toolMsgs).toHaveLength(1);
    if (toolMsgs[0].role === "tool") {
      expect(toolMsgs[0].tool_call_id).toBe("b");
    }
  });

  // ---------------------------------------------------------------------------
  // T1.3 — Empty assistant bubbles with no completed tool calls are omitted
  // ---------------------------------------------------------------------------
  it("T1.3 — omits empty assistant bubbles with no completed tool calls", () => {
    const input: ChatMessage[] = [
      { id: "u1", role: "user", content: "hello" },
      { id: "a1", role: "assistant", content: "" },
    ];

    const out = buildAllMessages(input, uuid);

    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({ id: "u1", role: "user", content: "hello" });
  });
});
