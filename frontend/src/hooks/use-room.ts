"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ChatImage } from "@/types/chat";

export type TimelineStep = { kind: "text" | "tool"; name?: string; status?: string; text?: string; callId?: string };
export type RoomState = {
  messages: any[];
  typing: boolean;
  timelines: Record<string, TimelineStep[]>;
  activeTurn: string | null;
};

/**
 * Pure reducer for a single stream event. Kept side-effect free so it can be
 * unit-tested in isolation (see __tests__/merge.test.ts, __tests__/timeline.test.ts).
 *
 * - `bot.typing` / `bot.done` toggle the typing indicator.
 * - `message` appends, deduping by id, and strips the transport `type` field.
 * - `agent.*` events accumulate a per-turn timeline of text/tool steps.
 * - Everything else (including `__closed__`) is ignored and returns `s` as-is.
 */
export function mergeEvent(s: RoomState, e: any): RoomState {
  if (e.type === "bot.typing") return { ...s, typing: true };
  if (e.type === "bot.done") return { ...s, typing: false };
  if (e.type === "agent.run.started") {
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: [] }, activeTurn: e.turn_id };
  }
  if (e.type === "agent.text.delta") {
    const prev = s.timelines[e.turn_id] ?? [];
    const last = prev[prev.length - 1];
    const steps = last?.kind === "text"
      ? [...prev.slice(0, -1), { ...last, text: (last.text ?? "") + e.delta }]
      : [...prev, { kind: "text" as const, text: e.delta }];
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: steps } };
  }
  if (e.type === "agent.tool.start") {
    const prev = s.timelines[e.turn_id] ?? [];
    return {
      ...s,
      timelines: {
        ...s.timelines,
        [e.turn_id]: [...prev, { kind: "tool" as const, name: e.name, status: "running", callId: e.call_id }],
      },
    };
  }
  if (e.type === "agent.tool.result") {
    const prev = s.timelines[e.turn_id] ?? [];
    let idx = e.call_id != null ? prev.findIndex((x) => x.kind === "tool" && x.callId === e.call_id) : -1;
    if (idx === -1) {
      // Fall back to matching by name on the most recent running step of that
      // name, for events that don't carry a call_id.
      const i = [...prev].reverse().findIndex((x) => x.kind === "tool" && x.name === e.name && x.status === "running");
      idx = i === -1 ? -1 : prev.length - 1 - i;
    }
    if (idx === -1) return { ...s, timelines: { ...s.timelines, [e.turn_id]: [...prev, { kind: "tool" as const, name: e.name, status: e.status }] } };
    const steps = prev.map((x, j) => (j === idx ? { ...x, status: e.status } : x));
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: steps } };
  }
  if (e.type === "agent.run.finished" || e.type === "agent.run.error") {
    // Timeline stays (collapses in UI); only clear activeTurn if this event
    // belongs to the turn that's currently marked live.
    if (s.activeTurn === e.turn_id) return { ...s, activeTurn: null };
    return s;
  }
  if (e.type === "message") {
    const { type, ...msg } = e;
    const existingIdx = s.messages.findIndex((m) => m.id === e.id);
    if (existingIdx !== -1) {
      // Most messages are immutable once published, so a repeat of a known id
      // is just a duplicate delivery to ignore. Expense drafts are the
      // exception: the backend re-publishes the SAME draft id with
      // attachments.status flipped (pending -> committed/cancelled) after
      // Record now / Cancel, so that update must replace the stored message
      // in place or the card would look pending forever.
      if (e.kind === "expense_draft") {
        return { ...s, messages: s.messages.map((m, i) => (i === existingIdx ? msg : m)) };
      }
      return s;
    }
    const withoutPending = s.messages.filter(
      (m) =>
        !(
          m.pending &&
          m.body === e.body &&
          (m.author?.id == null || m.author?.id === e.author?.id)
        ),
    );
    return { ...s, messages: [...withoutPending, msg] };
  }
  return s;
}

export function useRoom(roomId: number) {
  const [state, setState] = useState<RoomState>({ messages: [], typing: false, timelines: {}, activeTurn: null });
  const { signOut, memberId } = useSession();
  const lastId = useRef(0);

  useEffect(() => {
    const ac = new AbortController();
    let stop = false;
    lastId.current = 0;
    setState({ messages: [], typing: false, timelines: {}, activeTurn: null });

    (async () => {
      try {
        const { messages } = await api.getMessages(roomId, 0);
        messages.forEach((m: any) => (lastId.current = Math.max(lastId.current, m.id)));
        setState({ messages, typing: false, timelines: {}, activeTurn: null });
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          signOut();
          return;
        }
      }

      while (!stop) {
        try {
          await api.streamRoom(
            roomId,
            lastId.current,
            (e) => {
              // The hub emits {"type":"__closed__"} to signal end-of-stream;
              // it carries no message, so mergeEvent ignores it and the loop
              // simply reconnects on the next iteration.
              if (e && e.type === "__closed__") return;
              if (e && e.id) lastId.current = Math.max(lastId.current, e.id);
              setState((prev) => mergeEvent(prev, e));
            },
            ac.signal,
          );
        } catch (err) {
          // Session gone: stop looping and clear the session so the app falls
          // back to the sign-in / placeholder screen.
          if (err instanceof ApiError && err.status === 401) {
            if (!stop) signOut();
            return;
          }
          // Any other error (network drop, aborted fetch): fall through to the
          // reconnect delay below.
        }
        // Delay on EVERY iteration (success or error) to avoid a hot reconnect
        // loop when the stream closes cleanly and immediately.
        if (!stop) await new Promise((r) => setTimeout(r, 2000));
      }
    })();

    return () => {
      stop = true;
      ac.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId]);

  const send = (text: string, images?: ChatImage[]) => {
    // Optimistic echo: show the user's own message immediately instead of
    // waiting for the POST -> SSE round-trip. Reconciled (or marked errored)
    // once the real event arrives or the request fails; see mergeEvent's
    // "message" branch above for the pending-bubble dedupe.
    const tempId = -Date.now();
    setState((prev) => ({
      ...prev,
      messages: [
        ...prev.messages,
        { id: tempId, kind: "text", body: text, author: { id: memberId }, pending: true },
      ],
    }));
    return api.postMessage(roomId, text, images).catch((err) => {
      setState((prev) => ({
        ...prev,
        messages: prev.messages.map((m) => (m.id === tempId ? { ...m, error: true } : m)),
      }));
      throw err;
    });
  };

  return { messages: state.messages, typing: state.typing, timelines: state.timelines, activeTurn: state.activeTurn, send };
}
