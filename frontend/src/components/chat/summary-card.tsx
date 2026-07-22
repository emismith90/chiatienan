"use client";
import { BalanceBars } from "./balance-bars";
import { TransactionTimeline } from "./transaction-timeline";

export function SummaryCard({ attachments }: { attachments: any }) {
  return (
    <div className="mt-3 space-y-3">
      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Giao dịch</p>
        <TransactionTimeline events={attachments.timeline ?? []} />
      </div>
      {(attachments.balances ?? []).length > 0 && (
        <div className="border-t border-dashed border-[var(--border)] pt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Số dư</p>
          <BalanceBars rows={attachments.balances} />
        </div>
      )}
    </div>
  );
}
