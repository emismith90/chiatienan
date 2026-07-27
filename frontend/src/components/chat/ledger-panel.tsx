"use client";
import { useState } from "react";
import { useLedger } from "@/hooks/use-ledger";
import { BalanceBars } from "./balance-bars";
import { TransactionTimeline } from "./transaction-timeline";
import { StatementSections } from "./statement-card";

export function LedgerPanel({
  roomId, selfId, version, range, onClearRange,
}: {
  roomId: number;
  selfId: number | null;
  version: number;
  /** Explicit date range to show instead of the default window — set when a
   * history answer in the chat is opened here ("Mở sổ"). */
  range?: { from: string; to: string } | null;
  onClearRange?: () => void;
}) {
  const { data, loading } = useLedger(roomId, version, range);
  const [mine, setMine] = useState(false);
  const showMine = mine && selfId != null;

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-[var(--text-primary)]">Ledger</h2>
        {selfId != null && (
          <div className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
            <button type="button" onClick={() => setMine(false)}
                    className={`px-2.5 py-1 ${!mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Group
            </button>
            <button type="button" onClick={() => setMine(true)}
                    className={`px-2.5 py-1 ${mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Mine
            </button>
          </div>
        )}
      </div>

      {range && (
        <button type="button" onClick={onClearRange}
                className="flex items-center gap-1.5 self-start rounded-full border border-[var(--accent-primary)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--accent-text)]">
          {range.from} → {range.to}
          <span aria-hidden>✕</span>
          <span className="sr-only">Bỏ giới hạn ngày</span>
        </button>
      )}

      {loading && !data ? (
        <p className="text-xs text-[var(--text-secondary)]">Loading…</p>
      ) : showMine ? (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Balances</p>
            <BalanceBars rows={(data?.balances ?? []).filter((b) => b.id === selfId)} selfId={selfId} />
          </section>
          <StatementSections
            owe={data?.me?.owe ?? []} owed={data?.me?.owed ?? []} net={data?.me?.net ?? 0}
            roomId={roomId} onPaid={() => {}}
          />
        </>
      ) : (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Balances</p>
            <BalanceBars rows={data?.balances ?? []} selfId={selfId} />
          </section>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Transactions</p>
            <TransactionTimeline events={data?.timeline ?? []} />
          </section>
        </>
      )}
    </aside>
  );
}
