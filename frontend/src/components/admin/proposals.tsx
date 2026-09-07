/** The human half of the steward loop (plan Phase 12.2).
 *
 * A sub-agent with `cms: [read, draft]` can draft a change to its own profile and open a
 * proposal; it cannot publish. Approving here runs the same five gates as any publish and
 * then writes the source changes — publish first, sources second, so a gate failure
 * leaves no rewritten source behind (review F6). A refused approval keeps the proposal
 * pending with `last_error`, which is why that field is shown rather than swallowed.
 */
"use client";
import { useCallback, useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import { Badge, Notice, Pre, btn, btnPrimary, message, when } from "./ui";

export function Proposals({ reload }: { reload: () => Promise<void> }) {
  const [rows, setRows] = useState<admin.Proposal[]>([]);
  const [status, setStatus] = useState("pending");
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (s: string) => {
    setRows(await admin.proposals(s || undefined));
  }, []);

  useEffect(() => {
    load(status).catch((e) => setError(message(e)));
  }, [status, load]);

  async function decide(id: number, approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      await (approve ? admin.approveProposal(id) : admin.rejectProposal(id));
      await load(status);
      await reload();
    } catch (e) {
      setError(message(e));
      await load(status); // a refused approval writes last_error on the row
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Notice tone="error">{error}</Notice> : null}

      <div className="flex gap-2">
        {["pending", "approved", "rejected", ""].map((s) => (
          <button
            key={s || "all"}
            className={`${btn} ${status === s ? "bg-[var(--surface-2)]" : ""}`}
            onClick={() => setStatus(s)}
          >
            {s || "all"}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <Notice tone="info">
          Nothing here. An agent only opens a proposal when it has the `cms` capability and a
          manager delegates to it.
        </Notice>
      ) : null}

      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {rows.map((p) => (
          <li key={p.id}>
            <button
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
              onClick={() => setOpen(open === p.id ? null : p.id)}
              aria-expanded={open === p.id}
            >
              <span className="text-[var(--text)]">#{p.id}</span>
              <Badge tone={p.status === "pending" ? "warn" : "plain"}>{p.status}</Badge>
              <span className="text-[var(--text-secondary)]">
                profile {p.profile_id} · {(p.diff?.paths ?? []).join(", ") || "—"}
              </span>
              <span className="ml-auto text-[11px] text-[var(--text-secondary)]">{when(p.created_at)}</span>
            </button>

            {open === p.id ? (
              <div className="space-y-2 border-t border-[var(--border)] p-2">
                <p className="text-xs text-[var(--text)]">{p.rationale || "(no rationale given)"}</p>
                {p.last_error ? <Notice tone="warn">Last attempt: {p.last_error}</Notice> : null}
                {p.diff?.unified ? <Pre>{p.diff.unified}</Pre> : null}
                {p.source_changes?.length ? (
                  <p className="text-[11px] text-[var(--text-secondary)]">
                    Also rewrites {p.source_changes.length} source
                    {p.source_changes.length === 1 ? "" : "s"} on approval:{" "}
                    {p.source_changes.map((c: any) => `${c.kind}/${c.slug}`).join(", ")}
                  </p>
                ) : null}
                {p.status === "pending" ? (
                  <div className="flex gap-2">
                    <button className={btnPrimary} disabled={busy} onClick={() => void decide(p.id, true)}>
                      Approve and publish
                    </button>
                    <button className={btn} disabled={busy} onClick={() => void decide(p.id, false)}>
                      Reject
                    </button>
                  </div>
                ) : (
                  <p className="text-[11px] text-[var(--text-secondary)]">
                    {p.status} by {p.decided_by ?? "—"} on {when(p.decided_at)}
                  </p>
                )}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
