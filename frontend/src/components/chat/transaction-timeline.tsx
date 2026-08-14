"use client";
import { useState } from "react";
import { fmt } from "@/lib/format";
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

function EventRow({ e }: { e: TimelineEvent }) {
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
          <span className="font-medium text-[var(--text-primary)]">
            {e.from_name} → {e.to_name}
            <span className="ml-1 font-semibold text-[var(--accent-text)]">{fmt(e.amount)} đ</span>
          </span>
        )}
      </span>
    </li>
  );
}

function DaySection({ day }: { day: Day }) {
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
          <EventRow key={`${e.kind}-${e.kind === "meal" ? e.meal_id : e.payment_id}`} e={e} />
        ))}
      </ul>
    </section>
  );
}

export function TransactionTimeline({ events }: { events: TimelineEvent[] }) {
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
        <DaySection key={d.day} day={d} />
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
