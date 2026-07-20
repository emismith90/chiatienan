import type { ChatMessage } from "@/types/chat";

/**
 * AG-UI–shaped message sent to /api/copilot/agui.
 *
 * agno's convert_agui_messages_to_agno_messages expects:
 * - user:      {id, role:"user", content}
 * - assistant: {id, role:"assistant", content, tool_calls?: [{id, type:"function", function:{name, arguments}}]}
 * - tool:      {id, role:"tool", content, tool_call_id}
 *
 * A tool_call emitted on an assistant message MUST be followed by a ToolMessage
 * with matching tool_call_id — otherwise the agno filter silently drops the call.
 */
export type APIMessage =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "assistant";
      content: string;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
    }
  | { id: string; role: "tool"; content: string; tool_call_id: string };

/**
 * Convert frontend ChatMessage[] (display-oriented, multi-bubble) into the
 * AG-UI message history the backend/LLM expects.
 *
 * Key behaviors:
 * 1. `context` role messages are dropped (page-nav notices, UI-only).
 * 2. Consecutive `assistant` bubbles are coalesced into a single AssistantMessage
 *    with a joined content string and the union of their tool calls.
 *    The UI still renders separate bubbles; this coalescing is payload-only.
 * 3. Tool calls whose `result` is still null (e.g. stripped by the 2 MB
 *    localStorage cap in chat-storage.ts) are dropped from BOTH the
 *    assistant's tool_calls AND the trailing tool messages — otherwise
 *    agno's `tool_call_ids_with_results` filter drops the call anyway but
 *    leaves orphan IDs that can confuse ordering.
 * 4. Empty assistant bubbles (no content, no completed tool calls) are omitted
 *    so the LLM doesn't see phantom turns.
 */
export function buildAllMessages(
  messages: ChatMessage[],
  uuid: () => string,
): APIMessage[] {
  const out: APIMessage[] = [];
  // Drop UI-only roles: `context` (page-nav notices) and `reasoning` (the
  // agent's thinking trace — display-only, never replayed to the LLM). Leaving
  // `reasoning` in would also infinite-loop the coalescer below (it advances `i`
  // only for user/assistant).
  const filtered = messages.filter((m) => m.role !== "context" && m.role !== "reasoning");

  let i = 0;
  while (i < filtered.length) {
    const m = filtered[i];

    if (m.role === "user") {
      out.push({ id: m.id, role: "user", content: m.content });
      i++;
      continue;
    }

    // Coalesce consecutive assistant bubbles into one AssistantMessage.
    const group: ChatMessage[] = [];
    while (i < filtered.length && filtered[i].role === "assistant") {
      group.push(filtered[i]);
      i++;
    }

    const contentParts = group
      .map((g) => (g.content || "").trim())
      .filter((c) => c.length > 0);
    const content = contentParts.join(" ");

    const allToolCalls = group.flatMap((g) => g.toolCalls ?? []);
    // Only tool calls with a real result survive — see comment above.
    const completedToolCalls = allToolCalls.filter((tc) => tc.result !== null);

    // Skip entirely empty groups (phantom bubbles with no content and no data).
    if (!content && completedToolCalls.length === 0) continue;

    const assistantMsg: APIMessage = {
      id: group[0].id,
      role: "assistant",
      content,
      ...(completedToolCalls.length > 0
        ? {
            tool_calls: completedToolCalls.map((tc) => ({
              id: tc.id,
              type: "function" as const,
              function: { name: tc.name, arguments: tc.args },
            })),
          }
        : {}),
    };
    out.push(assistantMsg);

    for (const tc of completedToolCalls) {
      out.push({
        id: uuid(),
        role: "tool",
        content: tc.result ?? "",
        tool_call_id: tc.id,
      });
    }
  }

  return out;
}
