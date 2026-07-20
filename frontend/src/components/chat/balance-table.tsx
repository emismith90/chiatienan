"use client";
import { fmt } from "@/lib/format";

interface Row { id: number; name: string; paid: number; consumed: number; balance: number }

export function BalanceTable({ rows }: { rows: Row[] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
        Số dư hiện tại
      </p>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-[var(--text-secondary)]">
            <th className="py-1">Người</th>
            <th className="py-1 text-right">Đã trả</th>
            <th className="py-1 text-right">Đã ăn</th>
            <th className="py-1 text-right">Cân đối</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[var(--border)]">
              <td className="py-1 text-[var(--text-primary)]">{r.name}</td>
              <td className="py-1 text-right text-[var(--text-secondary)]">{fmt(r.paid)}</td>
              <td className="py-1 text-right text-[var(--text-secondary)]">{fmt(r.consumed)}</td>
              <td className={`py-1 text-right font-medium ${r.balance >= 0 ? "text-[var(--accent-text)]" : "text-[var(--danger)]"}`}>
                {r.balance >= 0 ? "+" : ""}{fmt(r.balance)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
