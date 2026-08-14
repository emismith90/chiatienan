"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import type { KnowledgeData } from "@/lib/api";
import {
  isConflict, primaryButtonClass, quietButtonClass, shortDate, writeError,
} from "./knowledge-ui";

/** `memory.md` as collapsible dated sections.
 *
 * The file is a chat summary the bot wrote about the room, appended to on `/clear`
 * and on the ten-week rollover. Rendering it as one blob would be the raw-text
 * failure this panel exists to fix; each `## header — date` becomes its own
 * section, and only the newest is open.
 *
 * The watermark is displayed and never editable. It is the lower bound of the
 * recent-message window the agent reads, so moving it backwards would make the next
 * turn re-summarize months of chat into duplicate sections — LLM-billed, and with
 * no undo.
 */
export function MemorySections({
  roomId, memory, etag, onSaved, onConflict,
}: {
  roomId: number;
  memory: KnowledgeData["memory"];
  etag: string;
  onSaved: () => void;
  onConflict: () => void;
}) {
  const newest = memory.sections.length - 1;
  const [open, setOpen] = useState<number | null>(newest >= 0 ? newest : null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function run(fn: Promise<unknown>, fail: string) {
    setBusy(true);
    setErr("");
    try {
      await fn;
      setEditing(null);
      setConfirmDelete(null);
      onSaved();
    } catch (e) {
      setErr(writeError(e, fail));
      if (isConflict(e)) onConflict();
    } finally {
      setBusy(false);
    }
  }

  if (memory.sections.length === 0) {
    return (
      <p className="text-xs text-[var(--text-secondary)]">
        No log entries yet. Phoenix writes one when you type <code>/clear</code>, and when
        the conversation gets older than 10 weeks.
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      {err && <p className="text-xs text-[var(--danger)]">{err}</p>}

      {memory.sections.map((s) => {
        const isOpen = open === s.index;
        return (
          <div key={s.index}
               className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)]">
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : s.index)}
              className="flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left"
            >
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--text-primary)]">
                {s.title}
              </span>
              <span className="shrink-0 text-[11px] text-[var(--text-secondary)]">
                {s.date ? shortDate(s.date) : "—"}
              </span>
            </button>

            {isOpen && (
              <div className="border-t border-[var(--border)] px-2.5 py-2">
                {editing === s.index ? (
                  <>
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      rows={8}
                      aria-label={`Edit entry ${s.title}`}
                      className="w-full rounded-md border border-[var(--border)] bg-transparent p-2 text-xs leading-relaxed text-[var(--text-primary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)]"
                    />
                    <div className="mt-2 flex gap-2">
                      <button type="button" disabled={busy}
                              onClick={() => run(
                                api.patchMemorySection(roomId, s.index, etag, draft),
                                "Could not save — try again.")}
                              className={primaryButtonClass}>
                        Save
                      </button>
                      <button type="button" disabled={busy} onClick={() => setEditing(null)}
                              className={quietButtonClass}>
                        Cancel
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="whitespace-pre-wrap text-xs leading-relaxed text-[var(--text-secondary)]">
                      {s.body}
                    </p>
                    <div className="mt-2 flex items-center gap-3">
                      <button type="button"
                              onClick={() => { setDraft(s.body); setEditing(s.index); }}
                              className="text-[11px] text-[var(--accent-text)]">
                        Edit
                      </button>
                      {confirmDelete === s.index ? (
                        <button type="button" disabled={busy}
                                onClick={() => run(
                                  api.deleteMemorySection(roomId, s.index, etag),
                                  "Could not delete.")}
                                className="text-[11px] font-medium text-[var(--danger)]">
                          Really delete?
                        </button>
                      ) : (
                        <button type="button" onClick={() => setConfirmDelete(s.index)}
                                className="text-[11px] text-[var(--danger)]">
                          Delete
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}

      {memory.watermark.through_at && (
        <p className="pt-1 text-[10px] text-[var(--text-secondary)]">
          Summarised through {memory.watermark.through_at.slice(0, 10)}. Anything newer
          Phoenix still reads directly.
        </p>
      )}
    </div>
  );
}
