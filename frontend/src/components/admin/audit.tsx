/** Who changed what, when (plan Phase 12.2). Newest first, `before`/`after` on demand. */
"use client";
import { useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import { Badge, Notice, Pre, btn, message, when } from "./ui";

export function Audit() {
  const [rows, setRows] = useState<admin.AuditRow[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [limit, setLimit] = useState(100);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    admin
      .audit(limit)
      .then(setRows)
      .catch((e) => setError(message(e)));
  }, [limit]);

  return (
    <div className="space-y-3">
      {error ? <Notice tone="error">{error}</Notice> : null}
      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {rows.map((r) => (
          <li key={r.id}>
            <button
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
              onClick={() => setOpen(open === r.id ? null : r.id)}
              aria-expanded={open === r.id}
            >
              <Badge>{r.action}</Badge>
              <span className="text-[var(--text)]">
                {r.entity} {r.entity_id}
              </span>
              <span className="text-[var(--text-secondary)]">{r.actor}</span>
              <span className="ml-auto text-[11px] text-[var(--text-secondary)]">{when(r.at)}</span>
            </button>
            {open === r.id ? (
              <div className="border-t border-[var(--border)] p-2">
                <Pre>{JSON.stringify({ before: r.before, after: r.after }, null, 1)}</Pre>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {rows.length >= limit ? (
        <button className={btn} onClick={() => setLimit(limit + 200)}>
          Show more
        </button>
      ) : null}
    </div>
  );
}
