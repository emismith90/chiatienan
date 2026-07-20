"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ChatImage } from "@/types/chat";

export type TimelineStep = { kind: "text" | "tool"; name?: string; status?: string; text?: string };
export type RoomState = { messages: any[]; typing: boolean; timelines: Record<string, TimelineStep[]> };

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
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: [] } };
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
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: [...prev, { kind: "tool" as const, name: e.name, status: "running" }] } };
  }
  if (e.type === "agent.tool.result") {
    const prev = s.timelines[e.turn_id] ?? [];
    const i = [...prev].reverse().findIndex((x) => x.kind === "tool" && x.name === e.name && x.status === "running");
    if (i === -1) return { ...s, timelines: { ...s.timelines, [e.turn_id]: [...prev, { kind: "tool" as const, name: e.name, status: e.status }] } };
    const idx = prev.length - 1 - i;
    const steps = prev.map((x, j) => (j === idx ? { ...x, status: e.status } : x));
    return { ...s, timelines: { ...s.timelines, [e.turn_id]: steps } };
  }
  if (e.type === "agent.run.finished" || e.type === "agent.run.error") return s; // timeline stays; collapses in UI
  if (e.type === "message") {
    if (s.messages.some((m) => m.id === e.id)) return s;
    const { type, ...msg } = e;
    return { ...s, messages: [...s.messages, msg] };
  }
  return s;
}

export function useRoom(roomId: number) {
  const [state, setState] = useState<RoomState>({ messages: [], typing: false, timelines: {} });
  const { signOut } = useSession();
  const lastId = useRef(0);

  useEffect(() => {
    const ac = new AbortController();
    let stop = false;
    lastId.current = 0;
    setState({ messages: [], typing: false, timelines: {} });

    (async () => {
      try {
        const { messages } = await api.getMessages(roomId, 0);
        messages.forEach((m: any) => (lastId.current = Math.max(lastId.current, m.id)));
        setState({ messages, typing: false, timelines: {} });
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

  const send = (text: string, images?: ChatImage[]) =>
    api.postMessage(roomId, text, images);

  return { messages: state.messages, typing: state.typing, timelines: state.timelines, send };
}
