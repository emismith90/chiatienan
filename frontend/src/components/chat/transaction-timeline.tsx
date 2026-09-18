"use client";
import { useState } from "react";
import { fmt } from "@/lib/format";
import * as api from "@/lib/api";
import type { TimelineEvent } from "@/lib/api";

/** How many days stay open before the rest collapse behind a toggle. */
const OPEN_DAYS = 3;

type Day = { day: string; events: TimelineEvent[]; spent: number; moved: number };

/** Group events by the day they happened, newest day first.
 *
 * A flat list stops being readable past a week — production had fifteen rows for
 * one week, and when someone asked for a day-by-day breakdown the bot reprinted
 * the same undivided paragraph twice. `spent` is what was fronted on meals that
 * day and `moved` is what changed hands in payments: different things, so they
 * are reported separately rather than added up.
 */
export function groupByDay(events: TimelineEvent[]): Day[] {
  const byDay = new Map<string, Day>();
  for (const e of events) {
    const day = byDay.get(e.occurred_on) ?? { day: e.occurred_on, events: [], spent: 0, moved: 0 };
    day.events.push(e);
    if (e.kind === "meal") day.spent += e.total;
    else day.moved += e.amount;
    byDay.set(e.occurred_on, day);
  }
  return [...byDay.values()].sort((a, b) => (a.day < b.day ? 1 : -1));
}

type PaymentEvent = Extract<TimelineEvent, { kind: "payment" }>;

/** The 💸 row's Undo, party-only and confirm-gated: tap once to arm it, again
 * to fire. A failure leaves it armed (not reverted to "Undo") so the retry is
 * one tap, not two — the same "leave the row untouched" shape as
 * `useMarkPaid` in statement-card.tsx, since undoing shared money deserves the
 * same care a mistaken tap there does. */
function PaymentRow({ e, selfId, roomId, onVoided }: {
  e: PaymentEvent;
  selfId?: number | null;
  roomId?: number;
  onVoided?: () => void;
}) {
  const [voided, setVoided] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const canUndo = roomId != null && selfId != null && (e.from_id === selfId || e.to_id === selfId);

  async function run() {
    if (busy) return;
    setBusy(true);
    setErr(false);
    try {
      await api.voidPayment(roomId!, e.payment_id);
      setVoided(true);
      onVoided?.();
    } catch {
      setErr(true); // stay in "Sure?" so the tap can be retried
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="font-medium text-[var(--text-primary)]">
      {e.from_name} → {e.to_name}
      <span className="ml-1 font-semibold text-[var(--accent-text)]">{fmt(e.amount)} đ</span>
      {voided && <span className="ml-1.5 text-[var(--text-secondary)]">· undone</span>}
      {canUndo && !voided && (
        confirming ? (
          <span className="ml-1.5 inline-flex items-center gap-1.5">
            <button type="button" disabled={busy} onClick={run}
                    className="text-[10px] font-semibold text-[var(--danger)]">
              {busy ? "…" : "Sure?"}
            </button>
            <button type="button" disabled={busy}
                    onClick={() => { setConfirming(false); setErr(false); }}
                    className="text-[10px] text-[var(--text-secondary)]">
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirming(true)}
                  aria-label={`Undo ${e.from_name} → ${e.to_name} ${fmt(e.amount)} đ`}
                  className="ml-1.5 text-[10px] text-[var(--accent-text)] underline">
            Undo
          </button>
        )
      )}
      {err && <span className="ml-1.5 text-[10px] font-medium text-[var(--danger)]">Failed — retry</span>}
    </span>
  );
}

function EventRow({ e, selfId, roomId, onVoided }: {
  e: TimelineEvent;
  selfId?: number | null;
  roomId?: number;
  onVoided?: () => void;
}) {
  return (
    <li className="grid grid-cols-[16px_1fr] items-baseline gap-2 text-xs">
      <span aria-hidden>{e.kind === "meal" ? "🍜" : "💸"}</span>
      <span className="min-w-0">
        {e.kind === "meal" ? (
          <>
            <span className="font-medium text-[var(--text-primary)]">{e.dish || "meal"}</span>
            <span className="block text-[var(--text-secondary)]">
              {e.payer_name} paid {fmt(e.total)} đ
            </span>
          </>
        ) : (
          <PaymentRow e={e} selfId={selfId} roomId={roomId} onVoided={onVoided} />
        )}
      </span>
    </li>
  );
}

function DaySection({ day, selfId, roomId, onVoided }: {
  day: Day;
  selfId?: number | null;
  roomId?: number;
  onVoided?: () => void;
}) {
  return (
    <section>
      <div className="mb-1 flex items-baseline justify-between border-b border-[var(--border)] pb-0.5">
        <h4 className="text-[11px] font-semibold text-[var(--text-primary)]">{day.day}</h4>
        <span className="text-[10px] text-[var(--text-secondary)]">
          {day.spent > 0 && `${fmt(day.spent)}đ on food`}
          {day.spent > 0 && day.moved > 0 && " · "}
          {day.moved > 0 && `${fmt(day.moved)}đ repaid`}
        </span>
      </div>
      <ul className="space-y-2">
        {day.events.map((e) => (
          <EventRow key={`${e.kind}-${e.kind === "meal" ? e.meal_id : e.payment_id}`} e={e}
                    selfId={selfId} roomId={roomId} onVoided={onVoided} />
        ))}
      </ul>
    </section>
  );
}

export function TransactionTimeline({ events, selfId, roomId, onVoided }: {
  events: TimelineEvent[];
  /** The signed-in member — gates the 💸 row's Undo to the two people the
   * money moved between. No Undo at all without it (e.g. before sign-in). */
  selfId?: number | null;
  roomId?: number;
  /** Fired after a successful undo, same role `onPaid` plays in
   * statement-card.tsx — the panel's own refresh rides the room's
   * `ledger:changed` stream event, not this callback. */
  onVoided?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!events || events.length === 0) {
    return <p className="text-xs text-[var(--text-secondary)]">No transactions this period.</p>;
  }
  const days = groupByDay(events);
  const shown = expanded ? days : days.slice(0, OPEN_DAYS);
  const hidden = days.length - shown.length;

  return (
    <div className="flex flex-col gap-3">
      {shown.map((d) => (
        <DaySection key={d.day} day={d} selfId={selfId} roomId={roomId} onVoided={onVoided} />
      ))}
      {hidden > 0 && (
        <button type="button" onClick={() => setExpanded(true)}
                className="self-start text-xs font-medium text-[var(--accent-text)] underline">
          {hidden} earlier day{hidden === 1 ? "" : "s"}…
        </button>
      )}
      {expanded && days.length > OPEN_DAYS && (
        <button type="button" onClick={() => setExpanded(false)}
                className="self-start text-xs text-[var(--text-secondary)] underline">
          Collapse
        </button>
      )}
    </div>
  );
}
