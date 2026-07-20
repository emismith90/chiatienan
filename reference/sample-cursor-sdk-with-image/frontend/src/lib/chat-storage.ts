import type { ChatMessage } from "@/types/chat";

export interface StoredConversation {
  id: string; // threadId
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

const LS_KEY = "sample-chat-conversations";
const MAX_CONVERSATIONS = 50;
const MAX_STORAGE_BYTES = 2 * 1024 * 1024; // 2 MB

function readAll(): StoredConversation[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as StoredConversation[];
  } catch {
    return [];
  }
}

function writeAll(convs: StoredConversation[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(convs));
  } catch {
    // localStorage full — strip tool call results and retry
    const stripped = convs.map((c) => ({
      ...c,
      messages: c.messages.map((m) => ({
        ...m,
        toolCalls: m.toolCalls?.map((tc) => ({ ...tc, result: null })),
        images: m.images?.map((i) => ({ ...i, data: "" })),
      })),
    }));
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(stripped));
    } catch {
      // still too big — keep only metadata
      console.warn("[chat-storage] localStorage full — conversation not persisted after stripping");
    }
  }
}

export function getConversations(): StoredConversation[] {
  return readAll().sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getConversation(id: string): StoredConversation | null {
  return readAll().find((c) => c.id === id) ?? null;
}

export function saveConversation(conv: StoredConversation) {
  const convs = readAll();
  const idx = convs.findIndex((c) => c.id === conv.id);
  if (idx >= 0) {
    convs[idx] = conv;
  } else {
    convs.push(conv);
  }

  // Enforce max conversations — drop oldest
  convs.sort((a, b) => b.updatedAt - a.updatedAt);
  const trimmed = convs.slice(0, MAX_CONVERSATIONS);

  // Check size and strip tool results if too large
  const raw = JSON.stringify(trimmed);
  if (raw.length > MAX_STORAGE_BYTES) {
    const stripped = trimmed.map((c) => ({
      ...c,
      messages: c.messages.map((m) => ({
        ...m,
        toolCalls: m.toolCalls?.map((tc) => ({ ...tc, result: null })),
        images: m.images?.map((i) => ({ ...i, data: "" })),
      })),
    }));
    writeAll(stripped);
  } else {
    writeAll(trimmed);
  }
}

export function deleteConversation(id: string) {
  const convs = readAll().filter((c) => c.id !== id);
  writeAll(convs);
}

export function generateTitle(firstMessage: string): string {
  const cleaned = firstMessage.replace(/\s+/g, " ").trim();
  if (cleaned.length <= 50) return cleaned;
  const truncated = cleaned.slice(0, 50);
  const lastSpace = truncated.lastIndexOf(" ");
  return (lastSpace > 20 ? truncated.slice(0, lastSpace) : truncated) + "...";
}
