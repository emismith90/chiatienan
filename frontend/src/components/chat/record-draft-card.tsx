"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";

/**
 * Confirm card for changes Phoenix proposes to a data collection (places today; any
 * collection the data plane serves). The bot never writes these directly: it turns the
 * request into actions, and nothing is saved until someone presses Confirm here.
 *
 * Each action shows what it does and, field by field, the value before → after —
 * computed by the backend from the same record it re-checks on Confirm (if the record
 * changed meanwhile, Confirm is refused and nothing is saved).
 */

interface Change { field: string; before: unknown; after: unknown }
interface Action { headline: string; changes: Change[]; op: string; soft?: boolean }

function show(v: unknown): string {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function label(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function RecordDraftCard({ message, roomId }: { message: any; roomId: number }) {
  const att = message.attachments ?? {};
  const list: Action[] = att.actions ?? [];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statusLabel =
    att.status === "committed" ? "Saved"
    : att.status === "cancelled" ? "Dismissed"
    : att.status === "superseded" ? "Replaced by a newer proposal"
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
          {list.length === 1 ? "Confirm this change?" : `Confirm these ${list.length} changes?`}
        </span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <ul className="flex flex-col gap-2">
        {list.map((a, i) => (
          <li key={i} className="text-sm">
            <p className={`font-medium ${a.op === "delete" ? "text-[var(--danger)]" : "text-[var(--text-primary)]"}`}>
              {a.headline}
            </p>
            {a.changes.length > 0 && (
              <dl className="mt-1 flex flex-col gap-0.5 pl-3">
                {a.changes.map((c) => (
                  <div key={c.field} className="flex gap-2 text-xs">
                    <dt className="shrink-0 text-[var(--text-secondary)]">{label(c.field)}</dt>
                    <dd className="min-w-0 break-words text-[var(--text-primary)]">
                      {a.op !== "create" && (
                        <><span className="text-[var(--text-secondary)] line-through">{show(c.before)}</span>{" → "}</>
                      )}
                      <span>{show(c.after)}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        ))}
      </ul>

      {error && <p className="mt-2 text-xs text-[var(--danger)]">{error}</p>}

      {att.status === "pending" && (
        <div className="mt-3 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => run(api.commitDraft(roomId, message.id), "Couldn't save, please try again.")}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Confirm
          </button>
          <button type="button" disabled={busy}
            onClick={() => run(api.cancelDraft(roomId, message.id), "Couldn't dismiss, please try again.")}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
