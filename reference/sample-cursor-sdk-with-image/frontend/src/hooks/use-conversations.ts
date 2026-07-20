"use client";

import { useState, useCallback } from "react";
import type { ChatMessage } from "@/types/chat";
import {
  getConversations,
  getConversation,
  saveConversation,
  deleteConversation as deleteConv,
  generateTitle,
  type StoredConversation,
} from "@/lib/chat-storage";

export type { StoredConversation };

export function useConversations() {
  const [conversations, setConversations] = useState<StoredConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const loadConversations = useCallback(() => {
    setConversations(getConversations());
  }, []);

  const saveCurrentConversation = useCallback(
    (threadId: string, messages: ChatMessage[]) => {
      if (messages.length === 0) return;

      const existing = getConversation(threadId);
      const firstUserMsg = messages.find((m) => m.role === "user");
      const title = existing?.title || (firstUserMsg ? generateTitle(firstUserMsg.content) : "Untitled");

      saveConversation({
        id: threadId,
        title,
        messages,
        createdAt: existing?.createdAt ?? Date.now(),
        updatedAt: Date.now(),
      });

      setActiveConversationId(threadId);
    },
    []
  );

  const loadConversation = useCallback(
    (id: string): StoredConversation | null => {
      const conv = getConversation(id);
      if (conv) setActiveConversationId(id);
      return conv;
    },
    []
  );

  const removeConversation = useCallback(
    (id: string) => {
      deleteConv(id);
      if (activeConversationId === id) setActiveConversationId(null);
      setConversations((prev) => prev.filter((c) => c.id !== id));
    },
    [activeConversationId]
  );

  const startNewConversation = useCallback(() => {
    setActiveConversationId(null);
  }, []);

  return {
    conversations,
    activeConversationId,
    loadConversations,
    saveCurrentConversation,
    loadConversation,
    removeConversation,
    startNewConversation,
  };
}
