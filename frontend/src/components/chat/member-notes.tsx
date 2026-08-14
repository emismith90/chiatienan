"use client";
import { useState } from "react";
import { useKnowledge } from "@/hooks/use-knowledge";
import type { KnowledgeNote } from "@/lib/api";
import { SectionLabel } from "./knowledge-ui";
import { NoteDialog } from "./note-dialog";
import { NoteRow } from "./note-row";

/**
 * "What Phoenix remembers about this person", inside the member dialog.
 *
 * Same rows and the same editor as the knowledge panel — imported, not
 * reimplemented: two renderers for one file is how the two drift apart. This is
 * where someone actually asks the question ("why does it keep saying I change my
 * mind?"), so the answer belongs at the person, not only in a panel three taps away.
 *
 * Matched on `subject_id`, not on the label: two members can share a display name,
 * and the server already knows which row each note points at.
 */
export function MemberNotes({
  roomId, memberId, version,
}: {
  roomId: number;
  memberId: number;
  version: number;
}) {
  const { data, reload } = useKnowledge(roomId, version);
  const [editing, setEditing] = useState<KnowledgeNote | null>(null);
  const [adding, setAdding] = useState(false);

  const rows = (data?.observations ?? []).filter(
    (o) => o.subject_kind === "member" && o.subject_id === memberId,
  );
  const subject = data?.subjects.find((s) => s.kind === "member" && s.id === memberId);

  return (
    <div className="mt-4 border-t border-[var(--border)] pt-3">
      <SectionLabel>Phoenix nhớ gì</SectionLabel>
      {rows.length === 0 ? (
        <p className="text-xs text-[var(--text-secondary)]">Chưa có ghi nhớ nào.</p>
      ) : (
        <div className="space-y-0.5">
          {rows.map((n, i) => (
            <NoteRow key={`${n.id}-${i}`} note={n} onOpen={setEditing} />
          ))}
        </div>
      )}

      {subject && (
        <button type="button" onClick={() => setAdding(true)}
                className="mt-1.5 text-[11px] text-[var(--accent-text)]">
          ＋ Thêm ghi nhớ
        </button>
      )}

      {(editing || adding) && data && subject && (
        <NoteDialog
          roomId={roomId}
          note={editing}
          subjects={data.subjects}
          etag={data.etags.observations}
          presetSubject={subject.subject}
          onClose={() => { setEditing(null); setAdding(false); }}
          onSaved={reload}
          onConflict={reload}
        />
      )}
    </div>
  );
}
