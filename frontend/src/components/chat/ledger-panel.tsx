"use client";
import { useState } from "react";
import { useLedger } from "@/hooks/use-ledger";
import { OutstandingList } from "./outstanding-list";
import { TransactionTimeline } from "./transaction-timeline";
import { StatementSections } from "./statement-card";

export function LedgerPanel({
  roomId, selfId, version, range, onClearRange,
}: {
  roomId: number;
  selfId: number | null;
  version: number;
  /** Explicit date range to show instead of the default window — set when a
   * history answer in the chat is opened here (the "Mở sổ" chat action). */
  range?: { from: string; to: string } | null;
  onClearRange?: () => void;
}) {
  const { data, loading } = useLedger(roomId, version, range);
  /** Mine is the default view: the panel's job is "what do I owe, who owes me",
   * and the group tab is the thing you go looking for. It only holds once we know
   * who "mine" is — before sign-in there is no statement to show. */
  const [mine, setMine] = useState(true);
  const showMine = mine && selfId != null;

  return (
    // No outer <aside> and no visible heading: `SidePanel` owns the scroll
    // container and the tab strip that names this view ("Ledger").
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <h2 className="sr-only">Spending ledger</h2>
      <div className="flex items-center justify-end">
        {selfId != null && (
          <div className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
            <button type="button" onClick={() => setMine(true)}
                    className={`px-2.5 py-1 ${mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Mine
            </button>
            <button type="button" onClick={() => setMine(false)}
                    className={`px-2.5 py-1 ${!mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Group
            </button>
          </div>
        )}
      </div>

      {range && (
        <button type="button" onClick={onClearRange}
                className="flex items-center gap-1.5 self-start rounded-full border border-[var(--accent-primary)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--accent-text)]">
          {range.from} → {range.to}
          <span aria-hidden>✕</span>
          <span className="sr-only">Clear the date range</span>
        </button>
      )}

      {loading && !data ? (
        <p className="text-xs text-[var(--text-secondary)]">Loading…</p>
      ) : showMine ? (
        <StatementSections
          owe={data?.me?.owe ?? []} owed={data?.me?.owed ?? []}
          roomId={roomId} onPaid={() => {}}
        />
      ) : (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Who owes who</p>
            <OutstandingList rows={data?.outstanding ?? []} selfId={selfId} />
          </section>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Transactions</p>
            <TransactionTimeline events={data?.timeline ?? []} />
          </section>
        </>
      )}
    </div>
  );
}
