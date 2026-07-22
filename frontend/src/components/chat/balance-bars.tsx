"use client";
import { fmt } from "@/lib/format";
import type { BalanceRow } from "@/lib/api";

const signed = (n: number) => (n > 0 ? `+${fmt(n)}` : fmt(n));

export function BalanceBars({ rows, selfId }: { rows: BalanceRow[]; selfId?: number | null }) {
  if (!rows || rows.length === 0) return null;
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.balance)));
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const pct = Math.round((Math.abs(r.balance) / max) * 50); // half-width each side
        const isSelf = r.id === selfId;
        return (
          <li key={r.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 text-xs">
            <span
              data-self={isSelf || undefined}
              className={`truncate ${isSelf ? "font-semibold text-[var(--accent-text)]" : "text-[var(--text-primary)]"}`}
            >
              {r.name}
            </span>
            <span className="relative h-3 rounded-full bg-[var(--bg-base)]">
              <span className="absolute left-1/2 top-[-2px] bottom-[-2px] w-px bg-[var(--text-secondary)] opacity-50" />
              {r.balance < 0 && (
                <span className="absolute top-0 bottom-0 rounded-full bg-[#e06a4f]"
                      style={{ right: "50%", width: `${pct}%` }} />
              )}
              {r.balance > 0 && (
                <span className="absolute top-0 bottom-0 rounded-full bg-[#3f9e5a]"
                      style={{ left: "50%", width: `${pct}%` }} />
              )}
            </span>
            <span className={`tabular-nums font-semibold ${r.balance < 0 ? "text-[#c0492e]" : r.balance > 0 ? "text-[#2e7d46]" : "text-[var(--text-secondary)]"}`}>
              {signed(r.balance)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
