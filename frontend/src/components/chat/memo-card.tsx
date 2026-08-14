"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";

/**
 * Confirm card for a lunch note Phoenix wants to remember (or forget).
 *
 * Deliberately simpler than the money cards: no editable fields. A note is prose
 * about a place or a person, so there is nothing to recompute — you either agree
 * with the sentence or you don't.
 */
export function MemoCard({
  message, roomId,
}: { message: any; roomId: number }) {
  const att = message.attachments ?? {};
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const removing = att.action === "remove";
  const statusLabel =
    att.status === "committed" ? (removing ? "Forgotten" : "Remembered")
    : att.status === "cancelled" ? "Dismissed"
    : null;

  const run = (fn: Promise<unknown>, fail: string) => {
    setBusy(true);
    setError(null);
    fn.catch((e) => setError(e instanceof ApiError ? e.message : fail)).finally(() => setBusy(false));
  };

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">
          {removing ? "Forget this?" : "Remember this?"}
        </span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <p className="text-xs text-[var(--text-secondary)]">
        {att.subject_label ?? att.subject}
        {att.when === null && <span className="ml-1">· always</span>}
        {att.gate && <span className="ml-1 font-mono">· {att.gate}</span>}
      </p>
      <p className="mt-1 text-sm text-[var(--text-primary)]">{att.text}</p>

      {error && <p className="mt-2 text-xs text-[var(--danger)]">{error}</p>}

      {att.status === "pending" && (
        <div className="mt-2 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => run(api.commitMemo(roomId, message.id), "Couldn't save, please try again.")}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            {removing ? "Forget" : "Remember"}
          </button>
          <button type="button" disabled={busy}
            onClick={() => run(api.cancelMemo(roomId, message.id), "Couldn't dismiss, please try again.")}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
