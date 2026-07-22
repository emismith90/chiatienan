"use client";
import { fmt } from "@/lib/format";
import type { TimelineEvent } from "@/lib/api";

export function TransactionTimeline({ events }: { events: TimelineEvent[] }) {
  if (!events || events.length === 0) {
    return <p className="text-xs text-[var(--text-secondary)]">Chưa có giao dịch nào trong kỳ.</p>;
  }
  return (
    <ul className="space-y-2">
      {events.map((e) => (
        <li key={`${e.kind}-${e.kind === "meal" ? e.meal_id : e.payment_id}`}
            className="grid grid-cols-[16px_1fr_auto] items-baseline gap-2 text-xs">
          <span aria-hidden>{e.kind === "meal" ? "🍜" : "💸"}</span>
          <span className="min-w-0">
            {e.kind === "meal" ? (
              <>
                <span className="font-medium text-[var(--text-primary)]">{e.dish || "bữa ăn"}</span>
                <span className="block text-[var(--text-secondary)]">
                  {e.payer_name} trả {fmt(e.total)} đ
                </span>
              </>
            ) : (
              <span className="font-medium text-[var(--text-primary)]">
                {e.from_name} → {e.to_name}
                <span className="ml-1 font-semibold text-[var(--accent-text)]">{fmt(e.amount)} đ</span>
              </span>
            )}
          </span>
          <span className="text-[10px] text-[var(--text-secondary)]">{e.occurred_on.slice(5)}</span>
        </li>
      ))}
    </ul>
  );
}
