"use client";
import { useEffect, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import type { PlaceStats } from "@/lib/api";

/** Shared chrome for the knowledge panel's editors.
 *
 * The editors are centred dialogs rather than inline forms because the side panel
 * is 260–320px wide: a place has eleven editable fields, and a column that narrow
 * can host a *list* or a form, not both. Same shell as `ProfileDialog` —
 * backdrop click and Esc close, clicks inside do not.
 */
export function PanelDialog({
  label, onClose, children, footer,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
  /** The action row. Pinned below the scroll area rather than sitting at the end
   * of it: a place has eleven fields, so on a laptop-height viewport the form
   * overflows and a Save button inside the scroller is simply not on screen. */
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onClick={onClose}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85dvh] w-full max-w-sm flex-col rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] shadow-xl"
      >
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
        {footer && (
          <div className="shrink-0 border-t border-[var(--border)] p-4">{footer}</div>
        )}
      </div>
    </div>
  );
}

export const fieldClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

export const primaryButtonClass =
  "flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-40";

export const quietButtonClass =
  "rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] disabled:opacity-40";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
        {label}
      </span>
      {children}
    </label>
  );
}

/** A small labelled pill. `tone="accent"` for the one fact worth spotting first
 * (a price band, a closure); everything else stays muted so a card with five tags
 * does not read as five warnings. */
export function Chip({
  children, tone = "muted",
}: {
  children: ReactNode;
  tone?: "muted" | "accent" | "danger";
}) {
  const cls =
    tone === "accent"
      ? "border-[var(--accent-primary)] text-[var(--accent-text)]"
      : tone === "danger"
        ? "border-[var(--danger)] text-[var(--danger)]"
        : "border-[var(--border)] text-[var(--text-secondary)]";
  return (
    <span className={`inline-flex items-center rounded-full border px-1.5 py-px text-[10px] ${cls}`}>
      {children}
    </span>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
      {children}
    </p>
  );
}

/** "4 lần · 12 ngày trước", or "chưa ăn" — the ledger's own count, in words.
 *
 * Never editable and never re-derived here: the numbers arrive computed (design
 * D1), and this only chooses how to say them. */
export function visitLabel(stats: PlaceStats): string {
  if (!stats.times) return "chưa ăn lần nào";
  const days = stats.days_since;
  const when =
    days == null ? null : days === 0 ? "hôm nay" : days === 1 ? "hôm qua" : `${days} ngày trước`;
  return [`${stats.times} lần`, when].filter(Boolean).join(" · ");
}

const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

/** The weekday the room actually goes, as a word.
 *
 * Deliberately a sentence rather than a seven-bar chart: at this width a chart of
 * counts like [0,1,0,2,0,1,0] carries less than "hay ăn T5" does, and it would be
 * the only chart in the app. */
export function rhythmLabel(stats: PlaceStats): string | null {
  const counts = WEEKDAYS.map((_, i) => stats.weekday_counts?.[String(i)] ?? 0);
  const top = Math.max(...counts, 0);
  if (top < 2) return null;             // one visit is not a rhythm
  const days = counts.map((c, i) => (c === top ? WEEKDAYS[i] : null)).filter(Boolean);
  return `hay ăn ${days.join(", ")}`;
}

/** `"2026-08-20"` → `"20/8"`, the way the room writes dates. */
export function shortDate(iso: string | null): string {
  if (!iso) return "";
  const [, m, d] = iso.split("-");
  return m && d ? `${Number(d)}/${Number(m)}` : iso;
}

/** Turn a failed write into something a human can act on.
 *
 * The server's own detail is preferred over any phrasing invented here: it is
 * already Vietnamese and already specific ("«Quán Bé Bự» đã có trong danh sách",
 * "Có người vừa sửa ghi nhớ — hãy tải lại"). A 409 means two different things
 * across these routes — a taken place name, or a stale etag — so mapping the
 * status to one sentence would tell half the callers the wrong thing.
 */
export function writeError(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.message ? e.message : fallback;
}

/** Stale-etag conflict: the file moved under us, so the caller reloads. Only the
 * two file-backed stores can produce this; a place collision is also a 409 and is
 * *not* one of these, which is why the callers that reload are the ones that sent
 * an etag in the first place. */
export function isConflict(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409;
}
