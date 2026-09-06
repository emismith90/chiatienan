"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { fmt } from "@/lib/format";

/**
 * Fallback card for a draft kind this frontend has no bespoke card for
 * (a poker `game_draft`, whatever the next pack registers).
 *
 * The backend commits any registered kind through the same two generic routes,
 * so the buttons work without the frontend knowing the business; what it cannot
 * know is how the payload should READ, so it lists the fields as they come. A
 * pack that deserves better gets its own card (see `expense-draft-card.tsx`)
 * and stops falling through here. Without this, an unknown kind rendered as an
 * empty human bubble — the draft was uncommittable from the chat.
 */

/** Kernel-owned bookkeeping — `DraftKind.stamps` as the render stage applies them
 *  (backend `kernos/plugins/render.py`) plus the card's own type and status. None
 *  of it is part of what is being proposed; keep this in step with `stamps`. */
const HIDDEN = new Set(["type", "status", "turn_id", "raw_input", "logged_by"]);

const isPrimitive = (v: unknown) =>
  typeof v === "string" || typeof v === "number" || typeof v === "boolean";

/** An id is a number but not a quantity: grouped ("1.234") it reads as money. */
const isId = (key: string) => key === "id" || key.endsWith("_id");

function label(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** One plain value as text; `key` only decides whether a number is grouped. */
function scalar(key: string, v: any): string | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return isId(key) ? String(v) : fmt(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

/** A nested object on one line: "Member 1 · Buy in 1.000.000 · Cash out 1.400.000". */
function describe(o: Record<string, any>): string {
  return Object.entries(o)
    .map(([k, v]) => {
      const s = isPrimitive(v) ? scalar(k, v) : null;
      return s === null ? null : `${label(k)} ${s}`;
    })
    .filter(Boolean)
    .join(" · ");
}

/** The lines one field contributes. A list of objects gets a line EACH: a card a
 *  person confirms into the ledger has to show the per-person amounts, not "3
 *  items" — a poker `game_draft` writes debt edges between named members. */
function lines(key: string, v: any): string[] {
  if (Array.isArray(v)) {
    if (v.every(isPrimitive)) {
      const one = v.map((x) => scalar(key, x)).filter(Boolean).join(", ");
      return one ? [one] : [];
    }
    return v.map((x) => (isPrimitive(x) ? scalar(key, x) : describe(x))).filter(Boolean) as string[];
  }
  if (v !== null && v !== undefined && typeof v === "object") {
    const one = describe(v);
    return one ? [one] : [];
  }
  const s = scalar(key, v);
  return s === null ? [] : [s];
}

/** "game_draft" -> "Game"; a kind that is only "_draft" (or missing) -> "Draft". */
export function draftTitle(kind: string | undefined): string {
  const stem = (kind ?? "").replace(/_draft$/, "");
  return stem ? label(stem) : "Draft";
}

export function DraftCard({
  message, roomId,
}: { message: any; roomId: number }) {
  const att = message.attachments ?? {};
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statusLabel =
    att.status === "committed" ? "Recorded"
    : att.status === "cancelled" ? "Cancelled"
    : att.status === "superseded" ? "Replaced by a newer proposal"
    : null;

  const run = (fn: Promise<unknown>, fail: string) => {
    setBusy(true);
    setError(null);
    fn.catch((e) => setError(e instanceof ApiError ? e.message : fail)).finally(() => setBusy(false));
  };

  const rows = Object.entries(att)
    .filter(([k]) => !HIDDEN.has(k))
    .map(([k, v]) => [k, lines(k, v)] as const)
    .filter(([, ls]) => ls.length > 0);

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">
          {draftTitle(message.kind ?? att.type)}
        </span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      {message.body && (
        <p className="mb-2 whitespace-pre-wrap break-words text-sm text-[var(--text-primary)]">
          {message.body}
        </p>
      )}

      <dl className="flex flex-col gap-1">
        {rows.map(([k, ls]) => (
          <div key={k} className="flex gap-2 text-sm">
            <dt className="shrink-0 text-[var(--text-secondary)]">{label(k)}</dt>
            <dd className="flex min-w-0 flex-col break-words text-[var(--text-primary)]">
              {ls.map((line, i) => <span key={i}>{line}</span>)}
            </dd>
          </div>
        ))}
      </dl>

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
