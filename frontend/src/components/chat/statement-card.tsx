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
  // Two lines, not one. This row lives both in a chat card and in the 260px
  // ledger panel — which is now the panel's default view — and name + dish +
  // amount + button never fit across that width: the amount used to land on top
  // of the dish. Name/amount on top, dish/button under it, fits either column.
  return (
    <li className="px-3 py-2 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[var(--text-primary)]">{r.name}</span>
        <span className="shrink-0 font-medium text-[var(--text-secondary)]">{fmt(r.amount)} đ</span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-[var(--text-secondary)]">
          {r.dish || "meal"}{(paid || r.status === "paid") && " · paid"}
          {!paid && r.status === "partial" && " · partial"}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {err && <span className="text-xs font-medium text-[var(--danger)]">Failed — retry</span>}
          {onPaid && !paid && (
            <button type="button" onClick={pay} disabled={busy}
                    className="rounded-full border border-[var(--accent-primary)] px-2.5 py-0.5 text-xs font-semibold text-[var(--accent-text)] transition-colors hover:bg-[var(--bg-base)] disabled:opacity-50">
              {busy ? "…" : "Mark paid"}
            </button>
          )}
        </span>
      </div>
    </li>
  );
}

function OwedRow({ r }: { r: Row }) {
  return (
    <li className="px-3 py-2 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[var(--text-primary)]">{r.name}</span>
        <span className="shrink-0 font-medium text-[var(--text-secondary)]">{fmt(r.amount)} đ</span>
      </div>
      <p className="mt-0.5 truncate text-xs text-[var(--text-secondary)]">{r.dish || "meal"}</p>
    </li>
  );
}

/** The two owe/owed sections — and deliberately no total under them.
 *
 * A "Net −54.500đ" line used to close this card. It was the only figure here you
 * could not act on, and with debts in both directions it read as if they had been
 * offset, which the ledger never does. Pass `onPaid` (+ roomId) to enable the ⑦
 * "Mark paid" button on unpaid owe rows. Used by StatementCard and LedgerPanel. */
export function StatementSections({ owe, owed, roomId, onPaid }: {
  owe: Row[]; owed: Row[]; roomId: number; onPaid?: () => void;
}) {
  if (owe.length === 0 && owed.length === 0) {
    return (
      <p className="mt-2 text-xs text-[var(--text-secondary)]">
        Bạn không nợ ai, không ai nợ bạn.
      </p>
    );
  }
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
    </div>
  );
}

export function StatementCard({ attachments, roomId }: { attachments: any; roomId: number }) {
  return (
    <div className="mt-3">
      <StatementSections
        owe={attachments.owe ?? []} owed={attachments.owed ?? []}
        roomId={roomId} onPaid={() => {}}
      />
    </div>
  );
}
