"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ChatImage } from "@/types/chat";
import { defaultStore, flushOutbox, newRecord, type FlushResult, type OutboxStore, type OutboxRecord } from "@/lib/outbox";

const isOnline = () => (typeof navigator === "undefined" ? true : navigator.onLine !== false);

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

/** Optimistic bubble for a not-yet-acknowledged outgoing message. `pending` so
 * mergeEvent's dedupe reconciles it when the real message arrives; `queued`
 * marks it as waiting for connectivity (vs in-flight). */
function pendingBubble(id: number | string, body: string, images: ChatImage[] | undefined,
                       memberId: number | null, queued: boolean) {
  return {
    id, kind: "text", body,
    attachments: images && images.length ? { images } : undefined,
    author: { id: memberId }, pending: true, queued,
  };
}

export function useRoom(roomId: number) {
  const [state, setState] = useState<RoomState>({ messages: [], typing: false, timelines: {}, activeTurn: null });
  const { signOut, memberId } = useSession();
  const lastId = useRef(0);
  const storeRef = useRef<OutboxStore | null>(null);
  if (!storeRef.current) storeRef.current = defaultStore();
  // Lets `send` (outside the mount effect) kick the effect's outbox retry.
  const retryRef = useRef<() => void>(() => {});

  useEffect(() => {
    const ac = new AbortController();
    let stop = false;
    lastId.current = 0;
    setState({ messages: [], typing: false, timelines: {}, activeTurn: null });
    const store = storeRef.current!;

    // A queued message the server ultimately rejects (e.g. 4xx) must not retry
    // forever — flag its bubble and let the drain drop it. Network failures
    // (no ApiError) rethrow so the record stays queued for the next attempt.
    const postRecord = async (rec: OutboxRecord) => {
      try {
        await api.postMessage(rec.roomId, rec.body, rec.images);
      } catch (e) {
        if (e instanceof ApiError) {
          setState((prev) => ({
            ...prev,
            messages: prev.messages.map((m) =>
              m.pending && m.body === rec.body ? { ...m, error: true, queued: false } : m),
          }));
          return;
        }
        throw e;
      }
    };
    // Single-flight: the backend has no idempotency key, so two overlapping
    // drains (e.g. an `online` event firing mid-drain) could double-POST the
    // same queued record. A guard keeps only one drain in flight at a time.
    let flushing = false;
    const flush = async (): Promise<FlushResult | null> => {
      if (flushing) return null;
      flushing = true;
      try {
        return await flushOutbox(store, roomId, postRecord);
      } catch {
        return null; // store unavailable; leave records queued for the next attempt
      } finally {
        flushing = false;
      }
    };
    // A send can fail at the network level (fetch rejects) while
    // `navigator.onLine` stays true — switching Wi-Fi/hotspot, a captive
    // portal, a transient blip — and NO `online` event follows. Without a
    // self-driven retry the queued message would sit "waiting for network"
    // until a reload or room switch. So retry on a capped backoff while the
    // outbox still has records; `online`/mount reset it to an immediate try.
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retryDelay = 2000;
    const clearRetry = () => {
      if (retryTimer != null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };
    function armRetry() {
      if (retryTimer != null || stop) return;
      retryTimer = setTimeout(() => {
        retryTimer = null;
        drain();
      }, retryDelay);
    }
    const drain = async () => {
      const res = await flush();
      if (stop || !res) return;
      if (res.remaining > 0) {
        retryDelay = Math.min(retryDelay * 2, 15000); // still failing → back off
        armRetry();
      } else {
        retryDelay = 2000; // drained → reset for next time
      }
    };
    // `send` kicks a retry through this ref after it queues a failed message.
    retryRef.current = () => {
      retryDelay = 2000;
      armRetry();
    };
    const onOnline = () => {
      retryDelay = 2000;
      drain();
    };
    if (typeof window !== "undefined") window.addEventListener("online", onOnline);

    (async () => {
      try {
        const { messages } = await api.getMessages(roomId, 0);
        if (stop) return;
        messages.forEach((m: any) => (lastId.current = Math.max(lastId.current, m.id)));
        setState({ messages, typing: false, timelines: {}, activeTurn: null });
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          if (!stop) signOut();
          return;
        }
      }

      // Re-surface messages queued in a previous (offline) session, then try to
      // drain them now that we're mounted.
      try {
        const queued = await store.list(roomId);
        if (queued.length) {
          setState((prev) => ({
            ...prev,
            messages: [
              ...prev.messages,
              ...queued.map((r) => pendingBubble(r.id, r.body, r.images, memberId, true)),
            ],
          }));
        }
      } catch {
        /* store unavailable — ignore */
      }
      drain();

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
      clearRetry();
      if (typeof window !== "undefined") window.removeEventListener("online", onOnline);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId]);

  const send = (text: string, images?: ChatImage[]) => {
    // Optimistic echo: show the user's own message immediately instead of
    // waiting for the POST -> SSE round-trip. Reconciled (or marked
    // errored/queued) once the real event arrives or the request settles; see
    // mergeEvent's "message" branch above for the pending-bubble dedupe.
    const store = storeRef.current!;
    const tempId = -Date.now();
    const online = isOnline();
    setState((prev) => ({
      ...prev,
      messages: [...prev.messages, pendingBubble(tempId, text, images, memberId, !online)],
    }));

    // Offline: persist to the outbox and resolve so the composer clears — the
    // bubble stays "queued" until the `online` handler (or next mount) drains it.
    if (!online) {
      store.add(newRecord(roomId, text, images, Date.now())).catch(() => {});
      retryRef.current(); // backstop in case the `online` event never fires
      return Promise.resolve();
    }

    return api.postMessage(roomId, text, images).catch((err) => {
      if (err instanceof ApiError) {
        // Server reached and rejected it — a real error; keep the composed text
        // (rethrow) so the user can fix and resend.
        setState((prev) => ({
          ...prev,
          messages: prev.messages.map((m) => (m.id === tempId ? { ...m, error: true } : m)),
        }));
        throw err;
      }
      // Network dropped mid-send: queue it and resolve (input clears), so it
      // flushes on reconnect instead of being lost. Kick the retry loop —
      // fetch can reject without navigator.onLine flipping, so we can't rely on
      // an `online` event arriving to drain it.
      store.add(newRecord(roomId, text, images, Date.now())).catch(() => {});
      retryRef.current();
      setState((prev) => ({
        ...prev,
        messages: prev.messages.map((m) => (m.id === tempId ? { ...m, queued: true } : m)),
      }));
    });
  };

  return { messages: state.messages, typing: state.typing, timelines: state.timelines, activeTurn: state.activeTurn, send };
}
