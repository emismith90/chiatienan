"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { buildAllMessages } from "@/lib/chat-payload";
import { agUiUrl } from "@/lib/api";
import type { ChatImage, ChatMessage, ToolCallEntry } from "@/types/chat";
export type { ChatImage, ChatMessage, ToolCallEntry };

function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  // Fallback for non-secure contexts (http://)
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export interface ChatOptions {
  /** Called after each completed exchange with the current threadId and full messages array. */
  onMessagesChange?: (threadId: string, messages: ChatMessage[]) => void;
  /** Per-request model id override (empty/undefined = Setup-page default). */
  model?: string;
}

export function useChat(options: ChatOptions = {}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toolCallMapRef = useRef<Map<string, ToolCallEntry>>(new Map());
  const threadIdRef = useRef(uuid());
  const abortRef = useRef<AbortController | null>(null);
  const onMessagesChangeRef = useRef(options.onMessagesChange);
  onMessagesChangeRef.current = options.onMessagesChange;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const sendMessage = useCallback(async (text: string, images?: ChatImage[]) => {
    const userMsg: ChatMessage = {
      id: uuid(),
      role: "user",
      content: text,
      ...(images && images.length > 0 ? { images } : {}),
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);
    setError(null);

    // Build message history for the agent.
    // Context messages (page navigation) are excluded — the current page
    // state is sent via forwardedProps into the system prompt.
    // Keeping them out preserves a stable user/assistant prefix for LLM
    // prompt caching. Context messages remain in local state for UI display.
    const allMessages = buildAllMessages([...messages, userMsg], uuid);

    const runId = uuid();
    const controller = new AbortController();
    abortRef.current = controller;

    let assistantId = "";
    let reasoningId = "";
    // Per-message text accumulators keyed by messageId.
    // Using a single `accumulated` variable breaks when TOOL_CALL_START
    // overwrites assistantId between text segments.
    const accumulators: Record<string, string> = {};

    function updateToolCallInMessages(toolCallId: string, updater: (tc: ToolCallEntry) => ToolCallEntry) {
      setMessages((prev) =>
        prev.map((m) => {
          if (!m.toolCalls) return m;
          const idx = m.toolCalls.findIndex((tc) => tc.id === toolCallId);
          if (idx === -1) return m;
          const updated = [...m.toolCalls];
          updated[idx] = updater(updated[idx]);
          return { ...m, toolCalls: updated };
        })
      );
    }

    try {
      const res = await fetch(agUiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          threadId: threadIdRef.current,
          runId,
          state: {},
          messages: allMessages,
          tools: [],
          context: [],
          forwardedProps: {
            model: optionsRef.current.model || undefined,
            // Current-turn image attachments (vision). Raw
            // base64 + mime; backend re-validates in _sanitize_images.
            images:
              images && images.length > 0
                ? images.map((i) => ({ data: i.data, mimeType: i.mimeType }))
                : undefined,
          },
        }),
      });

      if (!res.ok) {
        throw new Error(`Request failed: ${res.status}`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines: "data: {json}\n\n"
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith("data: ")) continue;

          let event: { type: string; messageId?: string; delta?: string; message?: string; name?: string; toolCallName?: string; toolCallId?: string; parentMessageId?: string; content?: string };
          try {
            event = JSON.parse(trimmed.slice(6));
          } catch {
            continue;
          }

          if (process.env.NODE_ENV === "development") {
            console.log("[agui]", event.type, event);
          }

          switch (event.type) {
            case "TEXT_MESSAGE_START": {
              // Always create a new assistant message for each text segment.
              // This splits reasoning, tool calls, and final answer into
              // separate bubbles (like Claude.ai multi-turn display).
              const newId = event.messageId ?? uuid();
              assistantId = newId;
              accumulators[newId] = "";
              setMessages((prev) => {
                // Guard: skip if a message with this ID already exists
                if (prev.some((m) => m.id === newId)) return prev;
                return [
                  ...prev,
                  { id: newId, role: "assistant", content: "", isStreaming: true, runId },
                ];
              });
              break;
            }

            case "TEXT_MESSAGE_CONTENT": {
              // Use messageId from the event to target the correct bubble,
              // falling back to assistantId for servers that omit it.
              const targetId = event.messageId ?? assistantId;
              accumulators[targetId] = (accumulators[targetId] ?? "") + (event.delta ?? "");
              const text = accumulators[targetId];
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === targetId ? { ...m, content: text } : m
                )
              );
              break;
            }

            case "TEXT_MESSAGE_END": {
              // Mark streaming complete so renderables can be parsed.
              // Keep the message even if empty — tool calls may already be
              // attached or may arrive shortly after.
              const endId = event.messageId ?? assistantId;
              setMessages((prev) =>
                prev.map((m) => (m.id === endId ? { ...m, isStreaming: false } : m))
              );
              break;
            }

            case "TOOL_CALL_START": {
              const tcId = event.toolCallId ?? uuid();
              // Guard: skip duplicate tool call events
              if (toolCallMapRef.current.has(tcId)) break;
              const entry: ToolCallEntry = {
                id: tcId,
                name: event.toolCallName ?? event.name ?? "tool",
                args: "",
                result: null,
                status: "running",
                startedAt: Date.now(),
                endedAt: null,
              };
              toolCallMapRef.current.set(tcId, entry);
              // Attach to the last assistant message if it's a tool-call-only
              // bubble. If the last message has text content, create a new
              // bubble so tool calls are visually separate from reasoning text.
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "assistant" && !last.content) {
                  return prev.map((m, i) =>
                    i === prev.length - 1
                      ? { ...m, toolCalls: [...(m.toolCalls ?? []), entry] }
                      : m
                  );
                }
                // Create a new tool-call-only bubble
                const newMsg: ChatMessage = {
                  id: uuid(),
                  role: "assistant",
                  content: "",
                  toolCalls: [entry],
                  runId,
                };
                assistantId = newMsg.id;
                return [...prev, newMsg];
              });
              break;
            }

            case "TOOL_CALL_ARGS": {
              const tcId = event.toolCallId;
              if (!tcId) break;
              const existing = toolCallMapRef.current.get(tcId);
              if (existing) {
                existing.args += event.delta ?? "";
                toolCallMapRef.current.set(tcId, existing);
                updateToolCallInMessages(tcId, (tc) => ({ ...tc, args: existing.args }));
              }
              break;
            }

            case "TOOL_CALL_END": {
              const tcId = event.toolCallId;
              if (!tcId) break;
              const existing = toolCallMapRef.current.get(tcId);
              if (existing) {
                existing.status = "completed";
                existing.endedAt = Date.now();
                toolCallMapRef.current.set(tcId, existing);
                updateToolCallInMessages(tcId, (tc) => ({ ...tc, status: "completed", endedAt: Date.now() }));
              }
              break;
            }

            case "TOOL_CALL_RESULT": {
              const tcId = event.toolCallId;
              if (!tcId) break;
              const existing = toolCallMapRef.current.get(tcId);
              if (existing) {
                existing.result = event.content ?? null;
                toolCallMapRef.current.set(tcId, existing);
                updateToolCallInMessages(tcId, (tc) => ({ ...tc, result: event.content ?? null }));
              }
              break;
            }

            // --- Reasoning trace (agno reasoning step) ---
            // Rendered as a collapsible "Thinking" bubble; excluded from the
            // history sent back to the agent (see chat-payload.ts).
            // The sample backend does not emit reasoning events; this branch
            // is inert but kept for a faithful port.
            case "REASONING_START":
            case "REASONING_MESSAGE_START": {
              const rid = event.messageId ?? `reasoning-${runId}`;
              reasoningId = rid;
              if (!(rid in accumulators)) accumulators[rid] = "";
              setMessages((prev) =>
                prev.some((m) => m.id === rid)
                  ? prev
                  : [...prev, { id: rid, role: "reasoning", content: "", isStreaming: true, runId }]
              );
              break;
            }

            case "REASONING_MESSAGE_CONTENT": {
              const rid = event.messageId ?? reasoningId;
              if (!rid) break;
              accumulators[rid] = (accumulators[rid] ?? "") + (event.delta ?? "");
              const text = accumulators[rid];
              setMessages((prev) => prev.map((m) => (m.id === rid ? { ...m, content: text } : m)));
              break;
            }

            case "REASONING_MESSAGE_END":
            case "REASONING_END": {
              const rid = event.messageId ?? reasoningId;
              if (!rid) break;
              setMessages((prev) => prev.map((m) => (m.id === rid ? { ...m, isStreaming: false } : m)));
              break;
            }

            case "RUN_ERROR":
              setError(event.message ?? "Agent error");
              break;
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message ?? "Request failed");
      }
    } finally {
      setIsLoading(false);
      abortRef.current = null;
      // Finalize any still-streaming messages and remove empty orphans, then persist
      setMessages((current) => {
        const finalized = current
          .map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m))
          .filter((m) => {
            if (m.role === "assistant") return !!m.content || !!(m.toolCalls && m.toolCalls.length > 0);
            if (m.role === "reasoning") return !!m.content; // drop empty thinking bubbles
            return true;
          });
        onMessagesChangeRef.current?.(threadIdRef.current, finalized);
        return finalized;
      });
    }
  }, [messages]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
    setError(null);
    toolCallMapRef.current.clear();
    threadIdRef.current = uuid();
  }, []);

  const loadFromConversation = useCallback((savedMessages: ChatMessage[], threadId: string) => {
    setMessages(savedMessages);
    setError(null);
    toolCallMapRef.current.clear();
    threadIdRef.current = threadId;
  }, []);

  const getThreadId = useCallback(() => threadIdRef.current, []);

  const hasActiveToolCall = useMemo(
    () => messages.some((m) => m.toolCalls?.some((tc) => tc.status === "running")),
    [messages],
  );
  return { messages, isLoading, error, hasActiveToolCall, sendMessage, stop, clear, loadFromConversation, getThreadId };
}
