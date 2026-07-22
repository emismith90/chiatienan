"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { fmt } from "@/lib/format";
import type { PaymentDraft } from "@/types/chat";

interface Member { id: number; display_name: string }

export function PaymentDraftCard({
  message, members, roomId,
}: { message: any; members: Member[]; roomId: number }) {
  const att = message.attachments as PaymentDraft;
  const name = (id: number) => members.find((m) => m.id === id)?.display_name ?? "?";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statusLabel =
    att.status === "committed" ? "Recorded" : att.status === "cancelled" ? "Cancelled" : null;

  const run = (fn: Promise<unknown>, fail: string) => {
    setBusy(true);
    setError(null);
    fn.catch((e) => setError(e instanceof ApiError ? e.message : fail)).finally(() => setBusy(false));
  };

  const transfers = att.transfers ?? [];

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Payment</span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <div className="flex flex-col gap-1">
        {transfers.map((t, i) => (
          <p key={i} className="text-sm text-[var(--text-primary)]">
            {name(t.from_member_id)} → {name(t.to_member_id)}{" "}
            <span className="font-semibold text-[var(--accent-text)]">{fmt(t.amount)} đ</span>
          </p>
        ))}
      </div>

      {error && <p className="mt-2 text-xs text-[var(--danger)]">{error}</p>}

      {att.status === "pending" && (
        <div className="mt-2 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => run(api.commitDraft(roomId, message.id), "Couldn't record, please try again.")}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Confirm
          </button>
          <button type="button" disabled={busy}
            onClick={() => run(api.cancelDraft(roomId, message.id), "Couldn't cancel, please try again.")}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
