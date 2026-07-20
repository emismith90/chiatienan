"use client";

import { createContext, useContext, useState, useEffect, useCallback } from "react";

const MIN_WIDTH = 320;
const MAX_WIDTH_RATIO = 0.5;
const DEFAULT_WIDTH = 400;
const LS_KEY = "sample-chat-sidebar-width";

export interface ChatSidebarState {
  isOpen: boolean;
  isPinned: boolean;
  width: number;
  setOpen: (v: boolean) => void;
  setPinned: (v: boolean) => void;
  setWidth: (w: number) => void;
}

export const ChatSidebarContext = createContext<ChatSidebarState>({
  isOpen: false,
  isPinned: false,
  width: DEFAULT_WIDTH,
  setOpen: () => {},
  setPinned: () => {},
  setWidth: () => {},
});

export function useChatSidebar() {
  return useContext(ChatSidebarContext);
}

/** Provider hook — call once in the layout, pass result to ChatSidebarContext.Provider */
export function useChatSidebarState(): ChatSidebarState {
  // Default open: in this sample the whole page IS the chat (no external launcher).
  const [isOpen, setOpen] = useState(true);
  const [isPinned, setPinned] = useState(false);
  const [width, setWidthRaw] = useState(DEFAULT_WIDTH);

  // Read persisted width on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(LS_KEY);
      if (stored) {
        const w = parseInt(stored, 10);
        if (w >= MIN_WIDTH && w <= window.innerWidth * MAX_WIDTH_RATIO) {
          setWidthRaw(w);
        }
      }
    } catch {}
  }, []);

  const setWidth = useCallback((w: number) => {
    const maxW = Math.floor(window.innerWidth * MAX_WIDTH_RATIO);
    const clamped = Math.max(MIN_WIDTH, Math.min(w, maxW));
    setWidthRaw(clamped);
    try { localStorage.setItem(LS_KEY, String(clamped)); } catch {}
  }, []);

  return { isOpen, isPinned, width, setOpen, setPinned, setWidth };
}

export { DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH_RATIO };
