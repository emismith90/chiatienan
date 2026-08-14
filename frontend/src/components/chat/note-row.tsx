"use client";
import type { KnowledgeNote } from "@/lib/api";
import { Chip, shortDate } from "./knowledge-ui";

/** One remembered fact — a standing rule or a dated observation.
 *
 * The gate is the whole reason this is a component and not a line of text:
 * `order-by@11:30` is a schema, and a human should only ever meet it as "Đặt trước
 * 11:30". `stale` marks a line that is still in the file but past the window the
 * agent reads — shown rather than hidden, because a note nobody can see is a note
 * nobody can clean up.
 */
export function NoteRow({
  note, onOpen,
}: {
  note: KnowledgeNote;
  onOpen: (n: KnowledgeNote) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(note)}
      className="w-full rounded-md px-2 py-1.5 text-left transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
    >
      <p className={`text-[13px] leading-snug ${note.stale ? "text-[var(--text-secondary)]" : "text-[var(--text-primary)]"}`}>
        {note.text}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {note.gate_label && <Chip tone="accent">{note.gate_label}</Chip>}
        {note.standing ? (
          <Chip>luôn đúng</Chip>
        ) : (
          <Chip>{shortDate(note.when)}</Chip>
        )}
        {note.stale && <Chip>đã cũ, bot không đọc nữa</Chip>}
      </div>
    </button>
  );
}
