"use client";
import { fmt } from "@/lib/format";
import type { OutstandingRow } from "@/lib/api";

/** Every open debt in the room, one row per direction.
 *
 * This replaced the per-person balance bars. A bar said "Linh −54.500đ", which
 * is not a thing anyone can pay: you pay a person, for a meal. So the group view
 * says who owes whom, and a debt in each direction shows as two rows rather than
 * being silently cancelled into one signed number.
 */
export function OutstandingList({ rows, selfId }: {
  rows: OutstandingRow[]; selfId?: number | null;
}) {
  if (!rows || rows.length === 0) {
    return <p className="text-xs text-[var(--text-secondary)]">Không ai nợ ai.</p>;
  }
  return (
    <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
      {rows.map((r) => {
        const mine = r.debtor_id === selfId || r.creditor_id === selfId;
        return (
          <li key={`${r.debtor_id}-${r.creditor_id}`}
              data-self={mine || undefined}
              className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
            <span className="min-w-0 truncate">
              <span className={r.debtor_id === selfId
                ? "font-semibold text-[var(--accent-text)]"
                : "text-[var(--text-primary)]"}>{r.debtor_name}</span>
              <span className="mx-1 text-[var(--text-secondary)]" aria-hidden>→</span>
              <span className={r.creditor_id === selfId
                ? "font-semibold text-[var(--accent-text)]"
                : "text-[var(--text-primary)]"}>{r.creditor_name}</span>
            </span>
            <span className="shrink-0 tabular-nums font-medium text-[var(--text-secondary)]">
              {fmt(r.amount)} đ
            </span>
          </li>
        );
      })}
    </ul>
  );
}
