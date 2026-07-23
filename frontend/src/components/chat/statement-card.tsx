"use client";
import { useState } from "react";
import { fmt } from "@/lib/format";
import * as api from "@/lib/api";

interface Row {
  other_id?: number; creditor_id?: number; debtor_id?: number;
  name: string; meal_id: number; dish: string | null; amount: number; status: string;
}

function OweRow({ r, roomId, onPaid }: { r: Row; roomId: number; onPaid?: () => void }) {
  const [paid, setPaid] = useState(r.status === "paid");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const creditorId = r.creditor_id ?? r.other_id!;
  async function pay() {
    if (busy || paid) return;
    setBusy(true);
    setErr(false);
    try {
      await api.quickPay(roomId, creditorId, r.meal_id);
      setPaid(true);
      onPaid?.();
    } catch {
      /* leave as unpaid so the user can retry */
      setErr(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
      <span className="min-w-0">
        <span className="text-[var(--text-primary)]">{r.name}</span>
        <span className="ml-2 text-xs text-[var(--text-secondary)]">
          {r.dish || "meal"}{(paid || r.status === "paid") && " · paid"}
          {!paid && r.status === "partial" && " · partial"}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <span className="font-medium text-[var(--text-secondary)]">{fmt(r.amount)} đ</span>
        {err && <span className="text-xs font-medium text-[var(--danger)]">Failed — retry</span>}
        {onPaid && !paid && (
          <button type="button" onClick={pay} disabled={busy}
                  className="rounded-full border border-[var(--accent-primary)] px-2.5 py-1 text-xs font-semibold text-[var(--accent-text)] transition-colors hover:bg-[var(--bg-base)] disabled:opacity-50">
            {busy ? "…" : "Mark paid"}
          </button>
        )}
      </span>
    </li>
  );
}

function OwedRow({ r }: { r: Row }) {
  return (
    <li className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
      <span className="min-w-0">
        <span className="text-[var(--text-primary)]">{r.name}</span>
        <span className="ml-2 text-xs text-[var(--text-secondary)]">{r.dish || "meal"}</span>
      </span>
      <span className="shrink-0 font-medium text-[var(--text-secondary)]">{fmt(r.amount)} đ</span>
    </li>
  );
}

/** Shared owe/owed sections + net. Pass `onPaid` (+ roomId) to enable the ⑦
 * "Mark paid" button on unpaid owe rows. Used by StatementCard and LedgerPanel. */
export function StatementSections({ owe, owed, net, roomId, onPaid }: {
  owe: Row[]; owed: Row[]; net: number; roomId: number; onPaid?: () => void;
}) {
  return (
    <div>
      {owe.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">You owe</p>
          <ul className="mt-1 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
            {owe.map((r) => <OweRow key={`o${r.meal_id}`} r={r} roomId={roomId} onPaid={onPaid} />)}
          </ul>
        </div>
      )}
      {owed.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Owed to you</p>
          <ul className="mt-1 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
            {owed.map((r) => <OwedRow key={`d${r.meal_id}`} r={r} />)}
          </ul>
        </div>
      )}
      <div className="mt-3 flex items-center justify-between border-t border-dashed border-[var(--border)] pt-2 text-sm">
        <span className="font-medium text-[var(--text-primary)]">Net</span>
        <span className={`font-semibold ${net < 0 ? "text-[#c0492e]" : net > 0 ? "text-[#2e7d46]" : "text-[var(--text-secondary)]"}`}>
          {net > 0 ? `+${fmt(net)}` : fmt(net)} đ
        </span>
      </div>
    </div>
  );
}

export function StatementCard({ attachments, roomId }: { attachments: any; roomId: number }) {
  return (
    <div className="mt-3">
      <StatementSections
        owe={attachments.owe ?? []} owed={attachments.owed ?? []} net={attachments.net ?? 0}
        roomId={roomId} onPaid={() => {}}
      />
    </div>
  );
}
