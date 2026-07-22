"use client";
import { useState } from "react";
import { useLedger } from "@/hooks/use-ledger";
import { BalanceBars } from "./balance-bars";
import { TransactionTimeline } from "./transaction-timeline";
import { StatementSections } from "./statement-card";

export function LedgerPanel({ roomId, selfId, version }: { roomId: number; selfId: number | null; version: number }) {
  const { data, loading } = useLedger(roomId, version);
  const [mine, setMine] = useState(false);
  const showMine = mine && selfId != null;

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-[var(--text-primary)]">Sổ nhóm</h2>
        {selfId != null && (
          <div className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
            <button type="button" onClick={() => setMine(false)}
                    className={`px-2.5 py-1 ${!mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Cả nhóm
            </button>
            <button type="button" onClick={() => setMine(true)}
                    className={`px-2.5 py-1 ${mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Của tôi
            </button>
          </div>
        )}
      </div>

      {loading && !data ? (
        <p className="text-xs text-[var(--text-secondary)]">Đang tải…</p>
      ) : showMine ? (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Số dư</p>
            <BalanceBars rows={(data?.balances ?? []).filter((b) => b.id === selfId)} selfId={selfId} />
          </section>
          <StatementSections
            owe={data?.me.owe ?? []} owed={data?.me.owed ?? []} net={data?.me.net ?? 0}
            roomId={roomId} onPaid={() => {}}
          />
        </>
      ) : (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Số dư</p>
            <BalanceBars rows={data?.balances ?? []} selfId={selfId} />
          </section>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Giao dịch</p>
            <TransactionTimeline events={data?.timeline ?? []} />
          </section>
        </>
      )}
    </aside>
  );
}
