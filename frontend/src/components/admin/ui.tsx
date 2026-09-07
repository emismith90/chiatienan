/** The small shared pieces of the admin screens (plan Phase 12.2). */
"use client";
import { ApiError } from "@/lib/api";
import { GateFailure } from "@/lib/admin-api";

export const box =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm " +
  "text-[var(--text)] disabled:opacity-60";
export const btn =
  "rounded-lg border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text)] " +
  "transition-colors hover:bg-[var(--surface-2)] disabled:opacity-50";
export const btnPrimary =
  "rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-xs text-white " +
  "transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-50";

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : "—";

export function Notice({ tone, children }: { tone: "warn" | "info" | "error"; children: React.ReactNode }) {
  const cls = {
    warn: "border-amber-500/40 bg-amber-500/10 text-amber-200",
    error: "border-red-500/40 bg-red-500/10 text-red-200",
    info: "border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-secondary)]",
  }[tone];
  return <div className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${cls}`}>{children}</div>;
}

export function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        {hint ? <p className="text-[11px] text-[var(--text-secondary)]">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span>
      {hint ? <span className="block text-[11px] text-[var(--text-secondary)]">{hint}</span> : null}
      {children}
    </label>
  );
}

export function Badge({ children, tone = "plain" }: { children: React.ReactNode; tone?: "plain" | "live" | "warn" }) {
  const cls = {
    plain: "border-[var(--border)] text-[var(--text-secondary)]",
    live: "border-emerald-500/40 text-emerald-300",
    warn: "border-amber-500/40 text-amber-300",
  }[tone];
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${cls}`}>{children}</span>;
}

/** A unified diff, or a JSON blob. Wide content scrolls inside its own box. */
export function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-2 text-[11px] leading-snug text-[var(--text-secondary)]">
      {children}
    </pre>
  );
}

/** One place that turns a thrown error into words an operator can act on. */
export function message(e: unknown): string {
  if (e instanceof GateFailure) return `Refused by ${e.gates.length === 1 ? "a gate" : "the gates"}:\n${e.message}`;
  if (e instanceof ApiError) {
    if (e.status === 401) return "That admin password was not accepted.";
    if (e.status === 409) return `Someone else changed this first — reload. (${e.message})`;
    if (e.status === 412) return "This was edited elsewhere since you loaded it — reload and redo your change.";
    return `${e.status}: ${e.message}`;
  }
  return (e as any)?.message ?? "something went wrong";
}
