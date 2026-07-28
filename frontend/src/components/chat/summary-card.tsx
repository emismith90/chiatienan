"use client";
import { OutstandingList } from "./outstanding-list";
import { TransactionTimeline } from "./transaction-timeline";

export function SummaryCard({
  attachments, onOpenLedger,
}: {
  attachments: any;
  /** Opens the ledger panel scoped to this summary's period. The chat body is a
   * one-line headline now, so the detail lives here and in the panel rather than
   * as a fifteen-row paragraph nobody can reformat. */
  onOpenLedger?: (range: { from: string; to: string }) => void;
}) {
  const period = attachments.period ?? {};
  return (
    <div className="mt-3 space-y-3">
      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Transactions</p>
        <TransactionTimeline events={attachments.timeline ?? []} />
      </div>
      {onOpenLedger && period.to && (
        <button type="button"
                onClick={() => onOpenLedger({ from: period.from ?? period.to, to: period.to })}
                className="text-xs font-medium text-[var(--accent-text)] underline">
          Mở sổ {period.from ? `${period.from} → ${period.to}` : `đến ${period.to}`}
        </button>
      )}
      {(attachments.outstanding ?? []).length > 0 && (
        <div className="border-t border-dashed border-[var(--border)] pt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Ai nợ ai</p>
          <OutstandingList rows={attachments.outstanding} />
        </div>
      )}
    </div>
  );
}
